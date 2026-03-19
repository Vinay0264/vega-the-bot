"""
actions/whatsapp.py — VEGA
═══════════════════════════════════════════════════════════════════
Sends WhatsApp messages via pywhatkit.
Reads contacts from contacts.txt.

CONTACT MATCHING — THREE-PASS:
  Pass 1 — substring match (instant, zero cost)
    "ravi" → finds "Ravi Kumar"
  Pass 2 — full name fuzzy via difflib (handles typos)
    "raavi", "ravi kumar" → still finds "Ravi Kumar"
  Pass 3 — word-level fuzzy (handles partial names)
    "ravi k", "kumar" → still finds "Ravi Kumar"
  No extra libraries. difflib is built into Python.

CONTACTS FILE FORMAT (contacts.txt):
  Name, +CountryCodeNumber
  Example:
    Ravi Kumar, +919876543210
    Mom, +919876543211

REQUIREMENTS:
  pip install pywhatkit
═══════════════════════════════════════════════════════════════════
"""

import sys
import difflib
import pywhatkit
from pathlib import Path

SCRIPT_DIR    = Path(__file__).parent  # vega root folder
CONTACTS_FILE = SCRIPT_DIR / "contacts.txt"

# ══════════════════════════════════════════════════════════════════════════════
#  CONTACTS — load and match
# ══════════════════════════════════════════════════════════════════════════════

def load_contacts() -> dict:
    """
    Load contacts from contacts.txt.
    Returns { "Name": "+91XXXXXXXXXX", ... }
    Creates a sample file if none exists.
    """
    if not CONTACTS_FILE.exists():
        CONTACTS_FILE.write_text(
            "# VEGA Contacts File\n"
            "# Format: Name, +CountryCodeNumber\n"
            "# Example:\n"
            "# Mom, +919876543210\n"
            "# Ravi Kumar, +919876543212\n",
            encoding="utf-8"
        )
        print(f"[WhatsApp] contacts.txt created at {CONTACTS_FILE}. Add your contacts and try again.")
        return {}

    contacts = {}
    for line in CONTACTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",", 1)
        if len(parts) == 2:
            name   = parts[0].strip()
            number = parts[1].strip()
            if name and number:
                contacts[name] = number
    return contacts


def find_contact(query: str, contacts: dict) -> tuple:
    """
    Three-pass contact lookup.

    Pass 1 — substring (case-insensitive, instant)
    Pass 2 — full name fuzzy via difflib (handles typos)
    Pass 3 — word-level fuzzy (handles partial names)

    Returns (name, number) of best match, or (None, None) if no match.
    """
    query_lower = query.lower().strip()

    # Pass 1 — substring
    matches = [
        (name, number)
        for name, number in contacts.items()
        if query_lower in name.lower()
    ]
    if matches:
        # Multiple matches — return shortest (most specific)
        return min(matches, key=lambda x: len(x[0]))

    # Pass 2 — full name fuzzy
    names = list(contacts.keys())
    close = difflib.get_close_matches(query, names, n=1, cutoff=0.5)
    if close:
        best = close[0]
        return best, contacts[best]

    # Pass 3 — word-level fuzzy
    # "ravi" matching "Ravi Kumar", "kumar" matching "Ravi Kumar"
    for name, number in contacts.items():
        words = name.lower().split()
        word_matches = difflib.get_close_matches(query_lower, words, n=1, cutoff=0.7)
        if word_matches:
            return name, number

    return None, None

# ══════════════════════════════════════════════════════════════════════════════
#  SEND MESSAGE — pywhatkit
# ══════════════════════════════════════════════════════════════════════════════

def send_message_to_contact(contact_query: str, message: str) -> dict:
    """
    Find contact and send WhatsApp message via pywhatkit.
    Called by brain.py.
    Returns: { success: bool, name: str, number: str, error?: str }
    """
    contacts = load_contacts()
    if not contacts:
        return {"success": False, "error": "No contacts found in contacts.txt"}

    name, number = find_contact(contact_query, contacts)
    if not name:
        return {
            "success": False,
            "error": f"No contact found matching '{contact_query}'. Check contacts.txt."
        }

    print(f"[WhatsApp] sending to {name} ({number})")

    try:
        pywhatkit.sendwhatmsg_instantly(
            phone_no=number,
            message=message,
            wait_time=20,    # intentional delay — avoids bot detection
            tab_close=True,
            close_time=5
        )
        print(f"[WhatsApp] message sent to {name}")
        return {"success": True, "name": name, "number": number}

    except Exception as e:
        return {
            "success": False,
            "name": name,
            "number": number,
            "error": str(e)
        }

# ══════════════════════════════════════════════════════════════════════════════
#  STANDALONE TEST — run this file directly to test
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    contacts = load_contacts()
    if not contacts:
        print("No contacts loaded.")
        sys.exit(1)

    print(f"Loaded {len(contacts)} contact(s):")
    for n, num in contacts.items():
        print(f"  {n} → {num}")

    query   = input("\nWho to message? >> ").strip()
    message = input("Message? >> ").strip()

    result = send_message_to_contact(query, message)
    if result["success"]:
        print(f"\nSent to {result['name']} ({result['number']})")
    else:
        print(f"\nFailed: {result['error']}")