"""
Run this once to permanently save your Anthropic API key.
Usage: python scripts/set_api_key.py
"""
import winreg
import os
import sys

def set_user_env(name, value):
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
    winreg.CloseKey(key)

print("AlphaPULSE — API Key Setup")
print("=" * 40)
print()

api_key = input("Paste your Anthropic API key (sk-ant-...): ").strip()
if not api_key.startswith("sk-ant-"):
    print("ERROR: Key should start with 'sk-ant-'. Please check and try again.")
    sys.exit(1)

set_user_env("ANTHROPIC_API_KEY", api_key)
print()
print("Done! ANTHROPIC_API_KEY saved permanently.")
print("Please CLOSE and REOPEN your Command Prompt, then run:")
print("  python scripts\\research_brief.py")
