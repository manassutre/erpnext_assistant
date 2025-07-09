# chatbot.py
"""AI‑powered assistant for ERPNext v15 sites
------------------------------------------------
This module replaces the previous proof‑of‑concept with a more robust engine
that can:
  • Generate SQL queries *or* Python snippets via Gemini 1.5
  • Execute queries safely and return tidy JSON
  • Fall back gracefully when a table is missing (no server‑side traceback)
  • Deliver tabular results as an Excel download on demand

Public endpoints:
  • get_solution(message)     → structured JSON response
  • get_response(message)     → alias for backward compatibility
  • download_solution(message)→ same as get_solution plus .xlsx link
  • download_response(user_input, bot_response) → compatible with JS handler
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Union

import frappe
import google.generativeai as genai
from dotenv import load_dotenv
from frappe.utils import get_url
from frappe.utils.file_manager import save_file
from openpyxl import Workbook
from frappe import throw

# --------------------------------------------------------------------------- #
# 1. Environment & Google Gemini configuration
# --------------------------------------------------------------------------- #

load_dotenv()
GOOGLE_API_KEY = frappe.db.get_value("Google API Key", {"default": 1}, "key")
if not GOOGLE_API_KEY:
    throw("Google Gemini API key not configured in DocType ‘Google API Key’. “key” field missing.")

genai.configure(api_key=GOOGLE_API_KEY)

# --------------------------------------------------------------------------- #
# 2. Gemini helper – ask the model for a solution
# --------------------------------------------------------------------------- #

def _ask_gemini(user_message: str) -> Dict[str, str]:
    # Friendly handling for greetings
    if user_message.strip().lower() in ["hi", "hello", "hey"]:
        return {
            "type": "explanation",
            "code": "Hello! 👋 How can I help you with your ERPNext data?",
            "note": "Friendly greeting."
        }

    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config={
            "temperature": 0,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 4096,
            "response_mime_type": "application/json",
        },
    )

    prompt = f"""
You are an expert Frappe/ERPNext developer with full access to the schema of an ERPNext v15 site (MariaDB).
A user will ask a question about their system.

Your tasks:
 1. Decide whether the request can be solved with a **SQL query** (use value `frappe_query`) that runs directly on the `tab*` tables, or requires a **Python snippet** (use value `python`) via Frappe ORM / API.  Anything else is `explanation`.

 2. If SQL is appropriate, write **one** valid MariaDB query that:
      • encloses table names and columns in back‑ticks (`),
      • uses clear `AS` aliases,
      • never references non‑existent tables.

 3. For Python solutions, return *only* the body of a function (no imports) that solves the request using the Frappe API.

 4. Respond with **pure JSON** in exactly the following structure:
{{
  "type": "frappe_query" | "python" | "explanation",
  "code": "…SQL/Python/Plain‑text…",
  "note": "…optional tip…"
}}

User request: "{user_message}"
"""

    response = model.generate_content(prompt)
    raw = response.text

    try:
        parsed = json.loads(raw)
        return parsed  # type: ignore[return-value]
    except json.JSONDecodeError:
        frappe.logger().warning("Gemini returned non‑JSON; fallback engaged.")
        return {
            "type": "explanation",
            "code": "Hello! I couldn’t understand that, but I’m here to help with ERPNext queries!",
            "note": "Gemini failed to format JSON."
        }

# --------------------------------------------------------------------------- #
# 3. Execution helpers – run SQL or just relay code/explanation
# --------------------------------------------------------------------------- #

def _execute_frappe_query(sql: str) -> Union[List[Dict[str, Any]], Dict[str, str]]:
    try:
        return frappe.db.sql(sql, as_dict=True)
    except Exception as exc:
        frappe.logger().error(f"[AI‑Agent] SQL failed → {exc}")
        return {
            "error": "Query execution failed on server.",
            "details": str(exc),
            "suggestion": "Please review the generated SQL or refine your question.",
        }

# --------------------------------------------------------------------------- #
# 4. Public controller – single source of truth for responses
# --------------------------------------------------------------------------- #

@frappe.whitelist()
def get_solution(message: str) -> Dict[str, Any]:
    gemini_resp = _ask_gemini(message)
    resp_type = gemini_resp.get("type")
    code = gemini_resp.get("code", "")
    note = gemini_resp.get("note", "")

    if resp_type == "frappe_query":
        data = _execute_frappe_query(code)
        return {
            "type": resp_type,
            "data": data,
            "query": code,
            "note": note,
        }

    return {
        "type": resp_type,
        "code": code,
        "note": note,
    }

@frappe.whitelist()
def get_response(message: str):
    return get_solution(message)

# --------------------------------------------------------------------------- #
# 5. Excel helpers
# --------------------------------------------------------------------------- #

def _records_to_excel(records: List[Dict[str, Any]]) -> str:
    wb = Workbook()
    ws = wb.active

    if records:
        ws.append(list(records[0].keys()))
        for row in records:
            ws.append(list(row.values()))

    file_path = os.path.join(frappe.get_site_path("public", "files"), "erp_chatbot_output.xlsx")
    wb.save(file_path)
    return file_path

def _save_and_get_url(file_path: str) -> str:
    filename = os.path.basename(file_path)
    return get_url(f"/files/{filename}")

@frappe.whitelist()
def download_solution(message: str) -> Dict[str, Any]:
    sol = get_solution(message)
    if sol.get("type") != "frappe_query" or isinstance(sol.get("data"), dict):
        return sol

    file_path = _records_to_excel(sol["data"])
    sol["file_url"] = _save_and_get_url(file_path)
    return sol

@frappe.whitelist()
def download_response(user_input: str, bot_response: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(bot_response)
        if isinstance(parsed, dict) and parsed.get("type") == "frappe_query":
            records = parsed.get("data", [])
            if not isinstance(records, list):
                return {"error": "Expected list of records under 'data' for Excel export."}
        elif isinstance(parsed, list):
            records = parsed
        else:
            return {"error": "Invalid format. Could not locate tabular data for Excel export."}

        file_path = _records_to_excel(records)
        url = _save_and_get_url(file_path)
        return {"file_url": url}
    except Exception as e:
        frappe.log_error(title="Download Response Error", message=str(e))
        return {"error": "Could not generate file."}
