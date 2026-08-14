# app/services/graph_analysis.py
"""
Money Flow Graph & Round-Trip Detection
------------------------------------------
Builds a directed graph from transactions:
    Nodes = accounts (the statement owner + counterparties extracted from narration)
    Edges = transactions, weighted by amount, tagged with date

Round-trip detection = finding cycles in this graph within a time window
(money that left account A eventually comes back to A through other accounts).

NOTE: Full round-trip detection needs statements from multiple accounts
uploaded together, since one account's statement only shows "money went
to X" - not what X did with it afterward. This module works with
whatever accounts are available and gets more powerful as more
statements are added to the same investigation.
"""

import re
import networkx as nx
from datetime import timedelta
from app.models.transaction import Transaction


def extract_counterparty(narration: str) -> str:
    """
    Bank narrations embed the other party's name/account in inconsistent
    ways depending on transaction type (UPI, NEFT, RTGS, IMPS, ATM).
    This does best-effort extraction using regex patterns per type.
    Falls back to a cleaned version of the full narration if no pattern matches.
    """
    n = narration.upper().strip()

    # UPI: "UPI/CR/C508616504762/KASTURI DU/OKIC/..."  -> name is 4th segment
    if n.startswith("UPI"):
        parts = narration.split("/")
        if len(parts) >= 4:
            return parts[3].strip()

    # NEFT/RTGS DR or CR: "...-BANDHAN BANK-CANARA BANK" or "...-SAGAR"
    # the counterparty name/bank is usually after the last hyphen
    if n.startswith(("NEFT", "RTGS")):
        parts = narration.split("-")
        if len(parts) >= 2:
            return parts[-1].strip()

    # IMPS: "IMPS-508611518864-DUBAI SHOPY-AIRP0000001-******84"
    # counterparty name is the 3rd hyphen-separated segment
    if n.startswith("IMPS"):
        parts = narration.split("-")
        if len(parts) >= 3:
            return parts[2].strip()

    # ATM withdrawal - not a transfer to another account, it's cash
    if "ATW" in n or "ATM" in n:
        return "CASH_WITHDRAWAL"

    # fallback: use narration itself, truncated
    return narration.strip()[:40]


def build_transaction_graph(transactions: list[Transaction], owner_account_id: str) -> nx.MultiDiGraph:
    """
    Builds a directed multigraph. Each transaction becomes one edge:
      - if it's a debit: owner_account -> counterparty
      - if it's a credit: counterparty -> owner_account
    """
    G = nx.MultiDiGraph()
    G.add_node(owner_account_id, label=owner_account_id)

    for t in transactions:
        counterparty = extract_counterparty(t.narration)
        if counterparty == "CASH_WITHDRAWAL":
            continue  # cash leaving the banking system isn't part of a traceable loop

        G.add_node(counterparty, label=counterparty)

        if t.debit > 0:
            G.add_edge(owner_account_id, counterparty, amount=t.debit, date=t.date,
                        narration=t.narration, ref_no=t.ref_no)
        if t.credit > 0:
            G.add_edge(counterparty, owner_account_id, amount=t.credit, date=t.date,
                        narration=t.narration, ref_no=t.ref_no)

    return G


def detect_round_trips(G: nx.MultiDiGraph, max_window_days: int = 30) -> list[dict]:
    """
    Finds simple cycles in the graph (money that eventually returns to
    where it started), constrained so all edges in a cycle happen within
    max_window_days of each other - a cycle spanning years isn't the
    same suspicious pattern as one spanning days.
    """
    round_trips = []
    for cycle in nx.simple_cycles(G):
        if len(cycle) < 2:
            continue
        # gather all edges along this cycle
        cycle_edges = []
        valid = True
        for i in range(len(cycle)):
            u, v = cycle[i], cycle[(i + 1) % len(cycle)]
            edge_data = G.get_edge_data(u, v)
            if not edge_data:
                valid = False
                break
            # take the first matching edge (multigraph can have several)
            first_edge = list(edge_data.values())[0]
            cycle_edges.append({"from": u, "to": v, **first_edge})
        if not valid or not cycle_edges:
            continue

        dates = [e["date"] for e in cycle_edges]
        if (max(dates) - min(dates)).days > max_window_days:
            continue

        round_trips.append({
            "accounts_involved": cycle,
            "edges": cycle_edges,
            "span_days": (max(dates) - min(dates)).days,
        })

    return round_trips


def find_accumulation_accounts(G: nx.MultiDiGraph, top_n: int = 5) -> list[dict]:
    """
    Finds accounts where money is net accumulating (received significantly
    more than sent) - potential destination/mule accounts.
    """
    net_flow = {}
    for node in G.nodes():
        inflow = sum(d["amount"] for _, _, d in G.in_edges(node, data=True))
        outflow = sum(d["amount"] for _, _, d in G.out_edges(node, data=True))
        net_flow[node] = inflow - outflow

    ranked = sorted(net_flow.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [{"account": acc, "net_accumulated": amt} for acc, amt in ranked if amt > 0]