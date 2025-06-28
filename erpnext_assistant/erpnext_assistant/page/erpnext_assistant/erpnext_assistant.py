# chatbot.py
import frappe

@frappe.whitelist()
def get_response(message):
    # Simulate bot response logic
    # Replace this logic with your actual response generation
    return f"""Create a SQL query for below prompt to run in the Frappe ERPnext. 
    '{message}'"""

# Copyright (c) 2024, Abhijeet Sutar and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class UploadCustomerPO(Document):
    pass

import frappe
import os
import google.generativeai as genai
from dotenv import load_dotenv
import json
import csv
import io
import re 
from frappe.utils import get_bench_path
from frappe import throw
from openpyxl import Workbook
from frappe.utils.file_manager import save_file
from frappe.utils import get_url
from frappe.utils.file_manager import save_file  

# # Load environment variables
load_dotenv()  # Load all environment variables including GOOGLE_API_KEY

google_api_key = frappe.db.get_value("Google API Key", {'default': 1}, "key")

# Set up Google Gemini API key
# genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
genai.configure(api_key=google_api_key)

# # # ------------------------------------------------------------------------------------------------------------------------------
# # # ------------------------------------------------------------------------------------------------------------------------------

# # # Generate the query based on user's input to run on the database------------------------------------------------------------------------------------------------------------------------------
@frappe.whitelist()
def generate_query(message, method=None):
    # # Generate content using the Gemini model with the uploaded file
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config={
            "temperature": 0,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 15000,  # Reduced token limit to avoid large responses
            "response_mime_type": "application/json",
        },
    )
    # Define the prompt with the file
    prompt = {
        "role": "user",
        "parts": [
            # f"""Create an SQL query to be run in Frappe ERPNext based on the message provided. The query should save the results in a variable named query. Ensure the columns are appropriately named according to the data being retrieved. Analyze the provided message to determine if the query can be built within the capabilities of ERPNext. If it is possible to create the query, set the type as 'frappe'. If the message does not relate to ERPNext or is not processable, set the type as 'other' and generate a generic response for the prompt, saving it in the query variable. Message is ''{message}''"""
            # f"""Create a SQL query based on below prompt to run in Frappe ERPnext ''{message}'' and save is in query variable. 
            # And give appropriate names to columns of sql query. Analyze the prompt and based on that give the response. If it is possible to build query in ERPnext based on promt then set type as frppe otherwise set another"""
            f"""
            You are an expert Frappe/ERPNext developer. A user has asked the following question related to their ERPNext system:

            "{message}"

            Your job is to:
            1. Interpret the intent behind the question — even if it's vague or not phrased like a typical SQL prompt.
            2. If the message can lead to a SQL query (e.g., data lookup, report, issue with entries), write the SQL and mark type as 'frappe'.
            3. If it is not solvable via SQL (like UI bug, printing issue, or unclear), then respond helpfully in plain English and set type as 'other'.

            Respond only with a valid JSON object in the format:

            {{
              "type": "frappe" or "other",
              "query": "The SQL query if applicable, or helpful message if not."
            }}

            Do not say 'too vague'. Try to assume user intent when possible.
            """
        ]
    }

    # # Generate the response
    chat_session = model.start_chat(history=[prompt])
    response = chat_session.send_message("Start processing")

    # # Log the raw response text for debugging
    response_text = response.text
    frappe.logger().info(f"Raw response from Gemini: {response_text[:1000]}")  # Log first 1000 chars for inspection

    # # Try to parse the JSON response
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError as e:
        frappe.throw(f"Failed to parse Gemini response: {str(e)}. Raw response: {response_text[:500]}")
    
    # # Check if the JSON is valid and complete
    if 'query' not in data:
        frappe.throw(f"Response from Gemini seems incomplete. Raw response: {response_text[:1000]}")
    # Extract individual variables from JSON response
    query = data.get("query", "N/A")
    type = data.get("type", "N/A")

    return query, type

# # # Run query on database------------------------------------------------------------------------------------------------------------------------------
@frappe.whitelist()
def run_sql_query(query):
    # Run the query on the database
    output = frappe.db.sql(query, as_dict=True)

    # Extract only the values from the output
    if output and isinstance(output, list):
        result = [list(row.values()) for row in output]
        return result

    # Return empty if no results found
    return "No data found"

@frappe.whitelist()
def to_donload_data(query):
    # Run the query on the database
    output = frappe.db.sql(query, as_dict=True)

    # Return empty if no results found
    return output


# # # Generate Responce------------------------------------------------------------------------------------------------------------------------------
@frappe.whitelist()
def get_response(message):
    # # Generate the query based on the user's prompt..
    query, type = generate_query(message)
    # frappe.msgprint(f"type: {type}")

    if type == "frappe":
        # # Generate the responce by running the query on database..
        response = run_sql_query(query)
    else:
        response = query
    return response 

# # # # # Download Responce------------------------------------------------------------------------------------------------------------------------------
# # Function to create the Excel file and return the file path
def create_excel_file(data):
    records = data
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"

    # Extract headers from the first record (keys of the dictionary)
    if records:
        headers = list(records[0].keys())
        sheet.append(headers)  # Add headers to the first row

    # Adding data rows
    for record in records:
        row = list(record.values())  # Extract values from the dictionary
        sheet.append(row)  # Append the row data

    # Define the file path
    file_path = "/tmp/erp_chatbot_output_.xlsx"
    
    # Save the workbook to the file path
    workbook.save(file_path)
    
    return file_path  # Ensure it returns the file path as a string

# # Function to handle the download process
def download_excel_file(data):
    # Create the Excel file
    file_path = create_excel_file(data)  # This should return the file path as a string
    
    # Open the file for reading
    with open(file_path, "rb") as filedata:
        file_name = "erp_chatbot_output_.xlsx"
        content = filedata.read()

    # Save the file in Frappe's file manager
    file_object = save_file(file_name, content, "Page", "ai-agent-1", is_private=False)
    
    # Generate the URL to the file
    file_url = get_url("/files/" + file_object.file_name)
    
    return file_url

# # Function to download the response
@frappe.whitelist()
def download_response(user_input, bot_response):
    # # Generate the query based on the user's prompt..
    query, type = generate_query(user_input)

    # # Generate the responce by running the query on database..
    data = to_donload_data(query)

    # file_url = create_excel_file
    url = download_excel_file(data)

    return {"file_url": url}
