"""
VEGA - WhatsApp Personal Assistant
=====================================
Supports contact names with fuzzy matching!
Type "ravi" and it finds "Ravi Kumar" automatically.

REQUIREMENTS:
  pip install pywhatkit

CONTACTS SETUP:
  Edit contacts.txt in the same folder as this script.
  Format:  Name, +CountryCodeNumber
  Example: Ravi Kumar, +919876543210

HOW TO RUN:
  python vega_whatsapp.py
"""

import pywhatkit
import os
import sys
import re


# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
CONTACTS_FILE = os.path.join(SCRIPT_DIR, "contacts.txt")


# ─────────────────────────────────────────────
#  LOAD CONTACTS FROM FILE
# ─────────────────────────────────────────────
def load_contacts():
    """Load contacts from contacts.txt into a dictionary."""
    contacts = {}

    if not os.path.exists(CONTACTS_FILE):
        print(f"⚠️  contacts.txt not found at: {CONTACTS_FILE}")
        print("   Creating a sample contacts.txt for you...")
        with open(CONTACTS_FILE, "w") as f:
            f.write("# VEGA Contacts File\n")
            f.write("# Format: Name, +CountryCodeNumber\n")
            f.write("# Example:\n")
            f.write("# Mom, +919876543210\n")
            f.write("# Ravi Kumar, +919876543212\n")
        print(f"   ✅ Created contacts.txt — please add your contacts and run again.\n")
        sys.exit(0)

    with open(CONTACTS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            # Split on first comma only
            parts = line.split(",", 1)
            if len(parts) == 2:
                name   = parts[0].strip()
                number = parts[1].strip()
                if name and number:
                    contacts[name] = number

    return contacts


# ─────────────────────────────────────────────
#  FUZZY CONTACT SEARCH
# ─────────────────────────────────────────────
def find_contact(query: str, contacts: dict):
    """
    Find contacts matching the query (case-insensitive, partial match).
    Returns list of (name, number) tuples that match.
    """
    query_lower = query.lower().strip()
    matches = []

    for name, number in contacts.items():
        if query_lower in name.lower():
            matches.append((name, number))

    return matches


# ─────────────────────────────────────────────
#  SEND MESSAGE
# ─────────────────────────────────────────────
def send_whatsapp_message(phone: str, message: str, name: str):
    try:
        print(f"\n⏳ Opening WhatsApp Web...")
        print(f"   Sending to {name} ({phone}) in 15 seconds...")
        print(f"   (Do not close the browser tab!)\n")

        pywhatkit.sendwhatmsg_instantly(
            phone_no=phone,
            message=message,
            wait_time=15,
            tab_close=True,
            close_time=3
        )

        print(f"✅ Message sent to {name}!\n")
        return True

    except Exception as e:
        print(f"❌ Failed to send: {e}\n")
        return False


# ─────────────────────────────────────────────
#  VEGA MAIN LOOP
# ─────────────────────────────────────────────
def vega():
    print("=" * 50)
    print("        VEGA — WhatsApp Personal Assistant")
    print("=" * 50)

    # Load contacts
    contacts = load_contacts()

    if not contacts:
        print("⚠️  No contacts found in contacts.txt")
        print(f"   Please add contacts to: {CONTACTS_FILE}")
        print("   Format: Name, +919876543210")
        sys.exit(0)

    print(f"\n✅ Loaded {len(contacts)} contact(s) from contacts.txt")
    print("\nType 'list' to see all contacts")
    print("Type 'quit' to exit\n")

    while True:
        try:
            print("─" * 45)

            # STEP 1: Contact name
            query = input("📇 Who do you want to message?\n   (Type name or part of name)\n   >> ").strip()

            if not query:
                print("⚠️  Please enter a name.\n")
                continue

            if query.lower() in ("quit", "exit", "q"):
                print("\n👋 Goodbye! Vega is shutting down.")
                break

            # Show all contacts
            if query.lower() == "list":
                print("\n📋 Your Contacts:")
                for name, number in contacts.items():
                    print(f"   {name} → {number}")
                print()
                continue

            # Fuzzy search
            matches = find_contact(query, contacts)

            if not matches:
                print(f"❌ No contact found matching '{query}'")
                print("   Try 'list' to see all contacts.\n")
                continue

            # If multiple matches, let user pick
            if len(matches) == 1:
                chosen_name, chosen_number = matches[0]
                print(f"   ✅ Found: {chosen_name} ({chosen_number})")
            else:
                print(f"\n🔍 Found {len(matches)} matches:")
                for i, (name, number) in enumerate(matches, 1):
                    print(f"   {i}. {name} ({number})")
                choice = input("\n   Which one? (Enter number) >> ").strip()
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(matches):
                        chosen_name, chosen_number = matches[idx]
                    else:
                        print("⚠️  Invalid choice.\n")
                        continue
                except ValueError:
                    print("⚠️  Please enter a number.\n")
                    continue

            # STEP 2: Message
            message = input(f"\n✏️  What message to send to '{chosen_name}'?\n   >> ").strip()

            if not message:
                print("⚠️  Message cannot be empty.\n")
                continue

            if message.lower() in ("quit", "exit", "q"):
                print("\n👋 Goodbye!")
                break

            # STEP 3: Confirm
            print(f"\n┌─────────────────────────────────────┐")
            print(f"  📤 To      : {chosen_name}")
            print(f"  📞 Number  : {chosen_number}")
            print(f"  💬 Message : {message}")
            print(f"└─────────────────────────────────────┘")
            confirm = input("  Send this? (y/n) >> ").strip().lower()

            if confirm != "y":
                print("❌ Cancelled.\n")
                continue

            # STEP 4: Send
            send_whatsapp_message(chosen_number, message, chosen_name)

            # STEP 5: Send another?
            again = input("📨 Send another message? (y/n) >> ").strip().lower()
            if again != "y":
                print("\n👋 Goodbye! Vega is shutting down.")
                break
            print()

        except KeyboardInterrupt:
            print("\n\n👋 Vega stopped.")
            break


# ─────────────────────────────────────────────
#  CALLABLE API — used by brain.py / server.py
# ─────────────────────────────────────────────
def send_message_to_contact(contact_query: str, message: str) -> dict:
    """
    Called by brain.py to send a WhatsApp message.
    Returns dict: { success: bool, name: str, number: str, error: str }
    """
    contacts = load_contacts()
    if not contacts:
        return {"success": False, "error": "No contacts found in contacts.txt"}

    matches = find_contact(contact_query, contacts)
    if not matches:
        return {"success": False, "error": f"No contact found matching '{contact_query}'"}

    # Pick best match (first/only)
    chosen_name, chosen_number = matches[0]

    try:
        pywhatkit.sendwhatmsg_instantly(
            phone_no=chosen_number,
            message=message,
            wait_time=15,
            tab_close=True,
            close_time=3
        )
        return {"success": True, "name": chosen_name, "number": chosen_number}
    except Exception as e:
        return {"success": False, "name": chosen_name, "number": chosen_number, "error": str(e)}


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    vega()