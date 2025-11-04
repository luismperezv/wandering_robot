#!/usr/bin/env python3

# Backwards-compatible entry point.
# The implementation has been refactored into modules; this file delegates to firmware.main.

def main():
	try:
		from firmware.main import main as _main
	except Exception:
		from main import main as _main  # type: ignore
	_main()

if __name__ == "__main__":
	main()
