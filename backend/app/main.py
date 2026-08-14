# app/main.py
"""
Bank Statement Analysis - API entry point.
Run with: uvicorn app.main:app --reload
Then visit http://127.0.0.1:8000/docs for interactive testing.
"""

import os
import shutil
import uuid
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.parsers.registry import parse_file
from app.services.cleaning import clean_transactions
from app.services.graph_analysis import build_transaction_graph, detect_round_trips, find_accumulation_accounts
from app.services.money_trail import trace_all_credits
from app.models.transaction import Transaction

app = FastAPI(title="Bank Statement Analysis System", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploaded_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# In-memory session store (swap for a real DB later)
SESSION_STORE: dict[str, list[Transaction]] = {}


@app.get("/")
def health_check():
    return {"status": "ok", "message": "Bank Statement Analysis API is running"}


@app.post("/upload")
async def upload_statement(file: UploadFile = File(...), account_id: str = Form("unknown")):
    """
    Upload one statement (PDF or markdown/text table). Extracts,
    cleans, and returns standardized transactions plus warnings.
    """
    saved_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}_{file.filename}")
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = parse_file(saved_path, account_id=account_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    cleaned = clean_transactions(result.transactions)

    session_id = str(uuid.uuid4())
    SESSION_STORE[session_id] = cleaned

    return {
        "session_id": session_id,
        "parser_used": result.parser_used,
        "transaction_count": len(cleaned),
        "duplicates_found": sum(1 for t in cleaned if t.is_duplicate),
        "balance_mismatches": sum(1 for t in cleaned if t.balance_mismatch),
        "reversed_transactions_flagged": sum(1 for t in cleaned if t.is_reversed_transaction),
        "warnings": result.warnings,
        "transactions": [t.model_dump(mode="json") for t in cleaned],
    }


@app.get("/transactions/{session_id}")
def get_transactions(session_id: str):
    if session_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"transactions": [t.model_dump(mode="json") for t in SESSION_STORE[session_id]]}


@app.get("/analysis/graph/{session_id}")
def get_graph_analysis(session_id: str, account_id: str = "unknown"):
    """Returns round-trip cycles and accumulation accounts for a session."""
    if session_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")

    transactions = SESSION_STORE[session_id]
    G = build_transaction_graph(transactions, owner_account_id=account_id)
    round_trips = detect_round_trips(G)
    accumulation = find_accumulation_accounts(G)

    return {
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "round_trips": round_trips,
        "accumulation_accounts": accumulation,
    }


@app.get("/analysis/money-trail/{session_id}")
def get_money_trail(session_id: str):
    """Traces every credit transaction forward through subsequent debits (FIFO)."""
    if session_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")

    transactions = SESSION_STORE[session_id]
    trails = trace_all_credits(transactions)
    return {"trails": trails}