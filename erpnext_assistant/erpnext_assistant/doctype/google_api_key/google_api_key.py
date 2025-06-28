# Copyright (c) 2025, Manas Sutre and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class GoogleAPIKey(Document):
	pass

def before_save(doc, method=None):
	if doc.default == 1:
		other_default = frappe.db.get_list("Google API Key", {"default": 1}, ["name"])
		for d in other_default:
			other_doc = frappe.get_doc("Google API Key", {'name': d.name})
			other_doc.default = 0
			other_doc.save()