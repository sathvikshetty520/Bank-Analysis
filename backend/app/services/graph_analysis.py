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

import networkx as nx
from app.models.transaction import Transaction


def extract_counterparty(narration: str) -> str:
    """
    Bank narrations embed the other party's name/account in inconsistent
    ways depending on transaction type AND bank. This is genuinely
    heuristic and bank-specific - expect to keep extending this as new
    banks are tested. Two known formats handled so far:
      - Bandhan Bank style: name after the LAST hyphen (NEFT/RTGS),
        or 3rd hyphen-segment (IMPS)
      - IDFC style: name is typically the segment before the IFSC
        code (2nd-to-last "/" segment), except for internal bulk-payment
        batches (BLKRTGS/BLKNEFT/BLKIFT) which have NO real counterparty
        name in the narration at all - these must NOT be mistaken for
        an account name (that was a real bug found in testing).
    """
    n = narration.upper().strip()

    if n.startswith(("BLKRTGS", "BLKNEFT", "BLKIFT")):
        return "INTERNAL_BULK_PAYMENT_BATCH"

    if n.startswith("UPI"):
        parts = narration.split("/")
        if len(parts) >= 4:
            candidate = parts[3].strip()
            if candidate.upper() in ("UPI", "NA", ""):
                return "UNKNOWN_UPI_COUNTERPARTY"
            return candidate

    if n.startswith(("RTGS/", "NEFT/")):
        parts = narration.split("/")
        if len(parts) >= 3:
            return parts[-2].strip()

    if n.startswith(("NEFT", "RTGS")):
        parts = narration.split("-")
        if len(parts) >= 2:
            return parts[-1].strip()

    if n.startswith("IMPS"):
        if "/" in narration:
            parts = narration.split("/")
            if len(parts) >= 3:
                return parts[2].strip()
        parts = narration.split("-")
        if len(parts) >= 3:
            return parts[2].strip()

    if "ATW" in n or "ATM" in n:
        return "CASH_WITHDRAWAL"

    return narration.strip()[:40]


def build_transaction_graph(transactions, owner_account_id):
    G = nx.MultiDiGraph()
    G.add_node(owner_account_id, label=owner_account_id)
    for t in transactions:
        counterparty = extract_counterparty(t.narration)
        if counterparty == "CASH_WITHDRAWAL":
            continue
        G.add_node(counterparty, label=counterparty)
        if t.debit > 0:
            G.add_edge(owner_account_id, counterparty, amount=t.debit, date=t.date, narration=t.narration, ref_no=t.ref_no)
        if t.credit > 0:
            G.add_edge(counterparty, owner_account_id, amount=t.credit, date=t.date, narration=t.narration, ref_no=t.ref_no)
    return G


def detect_round_trips(G, max_window_days=30):
    round_trips = []
    for cycle in nx.simple_cycles(G):
        if len(cycle) < 2:
            continue
        cycle_edges = []
        valid = True
        for i in range(len(cycle)):
            u, v = cycle[i], cycle[(i+1) % len(cycle)]
            edge_data = G.get_edge_data(u, v)
            if not edge_data:
                valid = False
                break
            first_edge = list(edge_data.values())[0]
            cycle_edges.append({"from": u, "to": v, **first_edge})
        if not valid or not cycle_edges:
            continue
        dates = [e["date"] for e in cycle_edges]
        if (max(dates) - min(dates)).days > max_window_days:
            continue
        round_trips.append({"accounts_involved": cycle, "edges": cycle_edges, "span_days": (max(dates)-min(dates)).days})
    return round_trips


def find_accumulation_accounts(G, top_n=5):
    net_flow = {}
    for node in G.nodes():
        if node == "INTERNAL_BULK_PAYMENT_BATCH" or node == "UNKNOWN_UPI_COUNTERPARTY":
            continue  # not real accounts - exclude from investigator-facing leads
        inflow = sum(d["amount"] for _, _, d in G.in_edges(node, data=True))
        outflow = sum(d["amount"] for _, _, d in G.out_edges(node, data=True))
        net_flow[node] = inflow - outflow
    ranked = sorted(net_flow.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [{"account": acc, "net_accumulated": amt} for acc, amt in ranked if amt > 0]