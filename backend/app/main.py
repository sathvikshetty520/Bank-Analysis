"""
Bank Statement Analysis - API entry point.
Run with: uvicorn app.main:app --reload
"""

import os
import shutil
import uuid
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.parsers.registry import parse_file
from app.services.cleaning import clean_transactions
from app.services.graph_analysis import build_multi_account_graph, detect_round_trips, find_accumulation_accounts
from app.services.money_trail import trace_all_credits
from app.services.report import generate_excel_report, generate_pdf_report
from app.models.transaction import Transaction

app = FastAPI(title="Bank Statement Analysis System", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UPLOAD_DIR = "uploaded_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# A session now holds MULTIPLE accounts' transactions, keyed by account_id,
# so an investigation can span several uploaded statements and detect
# genuine cross-account round-trips - not just a single account's view.
SESSION_STORE: dict[str, dict[str, list[Transaction]]] = {}


@app.get("/")
def health_check():
    return {"status": "ok", "message": "Bank Statement Analysis API is running"}


@app.post("/upload")
async def upload_statement(
    file: UploadFile = File(...),
    account_id: str = Form("unknown"),
    session_id: str = Form(None),
):
    """
    Upload a statement. If session_id is provided and exists, this
    file's transactions are ADDED to that session (enabling multi-
    account investigations) rather than starting a new one.
    """
    saved_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}_{file.filename}")
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = parse_file(saved_path, account_id=account_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    cleaned = clean_transactions(result.transactions)

    if session_id and session_id in SESSION_STORE:
        SESSION_STORE[session_id][account_id] = cleaned
    else:
        session_id = str(uuid.uuid4())
        SESSION_STORE[session_id] = {account_id: cleaned}

    return {
        "session_id": session_id,
        "account_id": account_id,
        "accounts_in_session": list(SESSION_STORE[session_id].keys()),
        "parser_used": result.parser_used,
        "transaction_count": len(cleaned),
        "duplicates_found": sum(1 for t in cleaned if t.is_duplicate),
        "balance_mismatches": sum(1 for t in cleaned if t.balance_mismatch),
        "reversed_transactions_flagged": sum(1 for t in cleaned if t.is_reversed_transaction),
        "reversed_confirmed": sum(1 for t in cleaned if t.reversal_confidence == "confirmed"),
        "reversed_possible": sum(1 for t in cleaned if t.reversal_confidence == "possible"),
        "warnings": result.warnings,
        "transactions": [t.model_dump(mode="json") for t in cleaned],
    }


def _all_transactions(session_id: str) -> list[Transaction]:
    """Flatten all accounts' transactions in a session into one list."""
    accounts = SESSION_STORE[session_id]
    all_txns = []
    for txns in accounts.values():
        all_txns.extend(txns)
    return all_txns


@app.get("/transactions/{session_id}")
def get_transactions(session_id: str):
    if session_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"transactions": [t.model_dump(mode="json") for t in _all_transactions(session_id)]}


@app.get("/analysis/graph/{session_id}")
def get_graph_analysis(session_id: str):
    if session_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")
    accounts = SESSION_STORE[session_id]
    G = build_multi_account_graph(accounts)
    round_trips = detect_round_trips(G)
    accumulation = find_accumulation_accounts(G)
    return {
        "accounts_in_session": list(accounts.keys()),
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "round_trips": round_trips,
        "accumulation_accounts": accumulation,
    }


@app.get("/analysis/money-trail/{session_id}")
def get_money_trail(session_id: str):
    if session_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")
    trails = trace_all_credits(_all_transactions(session_id))
    return {"trails": trails}


REPORTS_DIR = "generated_reports"
os.makedirs(REPORTS_DIR, exist_ok=True)


def _run_full_analysis(session_id: str):
    if session_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")
    accounts = SESSION_STORE[session_id]
    transactions = _all_transactions(session_id)
    G = build_multi_account_graph(accounts)
    round_trips = detect_round_trips(G)
    accumulation = find_accumulation_accounts(G)
    trails = trace_all_credits(transactions)
    return transactions, round_trips, accumulation, trails


@app.get("/report/excel/{session_id}")
def get_excel_report(session_id: str):
    transactions, round_trips, accumulation, trails = _run_full_analysis(session_id)
    output_path = os.path.join(REPORTS_DIR, f"{session_id}_report.xlsx")
    generate_excel_report(transactions, round_trips, accumulation, trails, output_path)
    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="investigation_report.xlsx",
    )


@app.get("/report/pdf/{session_id}")
def get_pdf_report(session_id: str):
    transactions, round_trips, accumulation, trails = _run_full_analysis(session_id)
    output_path = os.path.join(REPORTS_DIR, f"{session_id}_report.pdf")
    generate_pdf_report(transactions, round_trips, accumulation, trails, output_path)
    return FileResponse(
        output_path,
        media_type="application/pdf",
        filename="investigation_report.pdf",
    )