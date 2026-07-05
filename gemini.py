#!/usr/bin/env python3
"""
AI-native CLI for Gemini — flexible multimodal input via browser session.

Examples:
  python gemini.py "Explain quantum computing in 3 bullet points"
  python gemini.py -i chart.png "What trend does this show?"
  python gemini.py -i a.jpg -i b.jpg "Compare these two images"
  python gemini.py -f report.pdf "Summarize this document"
  python gemini.py -f data.csv -i plot.png "Analyze this data"
  cat prompt.txt | python gemini.py -i screenshot.png
  python gemini.py -i ui.png --brief -o review.md -q
  python gemini.py -l  "Ask a question after logging in via browser"

Multi-turn conversations:
  python gemini.py -c chat.json "My favorite color is blue."
  python gemini.py -c chat.json "What did I say my favorite color was?"
  python gemini.py -c chat.json --new  "Start a fresh conversation"
"""
import os
import sys
import json
import asyncio
import argparse
import re
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import loguru
loguru.logger.remove()
loguru.logger.add(sys.stderr, level="ERROR", format="<red>[gemini]</red> {message}")

import browser_cookie3
from gemini_webapi import GeminiClient

AUTH_EXPIRED_PATTERNS = [
    "UNAUTHENTICATED",
    "cookies have expired",
    "session is not authenticated",
    "error code: 1100",
    "User is not authenticated",
]

SEARCH_GEM_NAME = "Gemini search"
SEARCH_GEM_DESCRIPTION = "Headless Search Grounding Proxy — returns ultra-dense positional-array JSON (txt + img modes) for AI agent consumption"
SEARCH_GEM_PROMPT_FILE = Path(__file__).resolve().parent / "search-gem-prompt.txt"


class ChatRef:
    """Thin wrapper so gemini_webapi can read .metadata from the chat parameter."""
    def __init__(self, metadata: list):
        self.metadata = metadata


class GeminiCLI:
    def __init__(self):
        self.client = None
        self.quiet = False

    def log(self, msg: str):
        if not self.quiet:
            print(f"[gemini] {msg}", file=sys.stderr)

    def fail(self, code: str, reason: str):
        print(json.dumps({"ok": False, "err": code, "msg": reason}, ensure_ascii=False))
        sys.exit(1)

    # ── auth ──────────────────────────────────────────────

    def extract_cookies(self, preferred: str | None = None) -> tuple:
        # Linux/Mac: Chrome first (most common on desktop Linux & WSL).
        # Windows: Firefox first (no admin needed, most reliable cookie DB).
        if sys.platform == 'win32':
            browser_order = [
                ('firefox', browser_cookie3.firefox),
                ('chrome', browser_cookie3.chrome),
                ('edge', browser_cookie3.edge),
                ('safari', browser_cookie3.safari),
            ]
        else:
            browser_order = [
                ('chrome', browser_cookie3.chrome),
                ('firefox', browser_cookie3.firefox),
                ('edge', browser_cookie3.edge),
                ('safari', browser_cookie3.safari),
            ]

        # If a preferred browser is specified, try it first.
        # Accept env var GEMINI_BROWSER or --browser flag.
        if preferred:
            preferred_lower = preferred.lower()
            # Move preferred browser to front
            for i, (name, _) in enumerate(browser_order):
                if name == preferred_lower:
                    browser_order.insert(0, browser_order.pop(i))
                    self.log(f"Browser preference: {preferred_lower} (first)")
                    break
            else:
                self.log(f"Unknown browser '{preferred}'; ignored. Available: {', '.join(n for n, _ in browser_order)}")

        for name, fetch_func in browser_order:
            try:
                cj = fetch_func(domain_name='.google.com')
                sid, ts = None, None
                for c in cj:
                    if c.name == '__Secure-1PSID':
                        sid = c.value
                    elif c.name == '__Secure-1PSIDTS':
                        ts = c.value
                if sid:
                    self.log(f"Cookies from {name}")
                    return sid, ts
            except Exception:
                continue
        return None, None

    def is_auth_error(self, error_msg: str) -> bool:
        upper = error_msg.upper()
        return any(p.upper() in upper for p in AUTH_EXPIRED_PATTERNS)

    def _browser_login_flow(self, cookie_source: str) -> tuple[str | None, str | None]:
        """Open browser for Gemini login, poll for fresh cookies. Returns (sid, ts)."""
        if not sys.stdout.isatty():
            self.log("Not a TTY — can't start interactive login. Set GEMINI_SID/GEMINI_TS env vars.")
            return None, None

        self.log("Opening gemini.google.com for login...")
        webbrowser.open("https://gemini.google.com")

        self.log("Waiting for cookies (polling every 3s, 120s timeout)...")
        for i in range(40):
            time.sleep(3)
            new_sid, new_ts = self.extract_cookies(preferred=self._browser_pref)
            if new_sid:
                self.log(f"Cookies acquired after ~{(i + 1) * 3}s")
                return new_sid, new_ts
            if (i + 1) % 10 == 0:
                self.log(f"Still waiting... ({(i + 1) * 3}s)")
        return None, None

    # ── model resolution ──────────────────────────────────

    # Maps user shorthand to Model enum type component.
    # Thinking tier (--thinking) determines the prefix: BASIC / PLUS / ADVANCED.
    _MODEL_TYPE_ALIASES = {
        "pro": "PRO", "flash": "FLASH", "fast": "FLASH",
        "thinking": "THINKING", "think": "THINKING", "flash-thinking": "THINKING",
        "lite": "LITE",
    }

    _THINKING_ALIASES = {
        "standard": "BASIC", "basic": "BASIC",
        "plus": "PLUS",
        "extended": "ADVANCED", "advanced": "ADVANCED",
    }

    def resolve_model(self, user_input: str | None,
                      thinking: str | None = None):
        """Resolve model selection. Returns a Model enum when thinking tier
           is specified, otherwise a string. No hardcoded model names."""
        if not user_input:
            return None

        # ── Thinking tier specified → construct Model enum ──
        if thinking:
            tier = self._THINKING_ALIASES.get(thinking.lower().strip(), thinking.upper())
            mtype = self._MODEL_TYPE_ALIASES.get(user_input.lower().strip())
            if mtype is None:
                return user_input  # pass through, let server reject
            if mtype == "LITE":
                # Lite doesn't have thinking tiers, return as string
                return self._resolve_string(user_input)
            try:
                from gemini_webapi.client import Model
                return Model[f"{tier}_{mtype}"]
            except KeyError:
                return user_input

        # ── No thinking tier → string resolution (backward compat) ──
        return self._resolve_string(user_input)

    def _resolve_string(self, user_input: str) -> str:
        """Match user shorthand against live model list. Returns string model ID."""
        if self.client is None:
            return user_input
        try:
            available = self.client.list_models()
        except Exception:
            return user_input

        name_map = {str(m).lower(): str(m) for m in available}
        q = user_input.lower().strip()

        if q in name_map:
            return name_map[q]

        # Single substring match
        matches = [v for k, v in name_map.items() if q in k]
        if len(matches) == 1:
            return matches[0]

        # Alias matching — prefer gemini-* API names over display names
        def _prefer_api_name(models: list) -> str:
            gemini = [m for m in models if m.startswith("gemini-")]
            return gemini[0] if gemini else models[0]

        if q in ("flash", "fast", "speed"):
            flash = [v for k, v in name_map.items() if "flash" in k and "lite" not in k]
            if not flash:
                flash = [v for k, v in name_map.items() if "flash" in k]
            if flash:
                return _prefer_api_name(flash)
        if q in ("pro", "best", "smart"):
            pro = [v for k, v in name_map.items() if "pro" in k]
            if pro:
                return _prefer_api_name(pro)
        if q in ("lite", "cheap", "small"):
            lite = [v for k, v in name_map.items() if "lite" in k]
            if lite:
                return lite[0]

        return user_input

    # ── gem resolution ────────────────────────────────────

    def resolve_gem(self, user_input: str) -> str | None:
        """Resolve a Gem name or ID to a Gem ID string. Returns gem_id or raises."""
        if not user_input:
            return None
        try:
            gem = self.client.gems.get(name=user_input)
            if gem:
                return gem.id
            gem = self.client.gems.get(id=user_input)
            if gem:
                return gem.id
        except RuntimeError:
            pass
        # If gems not fetched or not found, pass through as raw ID
        return user_input

    async def fetch_and_list_gems(self):
        """Fetch gems and return a formatted string for display."""
        await self.client.fetch_gems()
        gems = self.client.gems
        lines = []
        for gid, g in gems.items():
            ptype = "system" if g.predefined else "user"
            lines.append(f"  [{ptype}] {g.name}  (id: {gid})")
            if g.description:
                lines.append(f"         {g.description}")
        return "\n".join(lines) if lines else "  No gems found."

    async def setup_search_gem(self):
        """Create or update the 'Gemini search' Gem with the optimized search grounding prompt."""
        prompt = SEARCH_GEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
        await self.client.fetch_gems()

        existing = self.client.gems.get(name=SEARCH_GEM_NAME)
        if existing:
            if existing.prompt and existing.prompt.strip() == prompt:
                self.log(f"Gem '{SEARCH_GEM_NAME}' already exists with current prompt (id: {existing.id})")
                return existing.id
            self.log(f"Updating Gem '{SEARCH_GEM_NAME}' (id: {existing.id})...")
            await self.client.update_gem(gem=existing.id, name=SEARCH_GEM_NAME,
                                         prompt=prompt, description=SEARCH_GEM_DESCRIPTION)
            self.log(f"Gem '{SEARCH_GEM_NAME}' updated.")
            return existing.id

        self.log(f"Creating Gem '{SEARCH_GEM_NAME}'...")
        gem = await self.client.create_gem(name=SEARCH_GEM_NAME, prompt=prompt,
                                           description=SEARCH_GEM_DESCRIPTION)
        self.log(f"Gem '{SEARCH_GEM_NAME}' created (id: {gem.id})")
        return gem.id

    # ── conversation state ────────────────────────────────

    def load_conversation(self, path: str) -> dict | None:
        p = Path(path)
        if not p.exists():
            return None
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
            if state.get("metadata") and len(state["metadata"]) >= 1:
                return state
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    def save_conversation(self, path: str, state: dict):
        state["updated"] = datetime.now(timezone.utc).isoformat()
        Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    # ── generate ──────────────────────────────────────────

    async def generate(self, sid: str, ts: str, prompt: str, files: list,
                       chat_metadata: list | None = None, model: str | None = None,
                       gem: str | None = None):
        """Returns (ok, response_text, new_metadata, images)."""
        if self.client is None:
            self.client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
            await self.client.init()
        try:
            kwargs = {"prompt": prompt}
            if files:
                kwargs["files"] = files
            if chat_metadata:
                kwargs["chat"] = ChatRef(chat_metadata)
            if model:
                kwargs["model"] = model
            if gem:
                kwargs["gem"] = gem
            response = await self.client.generate_content(**kwargs)
            new_meta = list(response.metadata) if response.metadata else None
            images = []
            try:
                for img in response.images:
                    images.append({"url": img.url, "alt": img.alt or ""})
            except Exception:
                pass
            return True, response.text, new_meta, images
        except Exception as e:
            return False, str(e), None, []

    # ── output ────────────────────────────────────────────

    def parse_code_blocks(self, text: str) -> list:
        pattern = r"```(\w*)\n(.*?)```"
        return [{"lang": m[0], "code": m[1].strip()}
                for m in re.findall(pattern, text, re.DOTALL)]

    def emit(self, text: str, args, conv_state: dict | None = None,
             images: list | None = None):
        code = self.parse_code_blocks(text)

        if args.output:
            out_path = Path(args.output)
            if out_path.suffix.lower() == ".json":
                payload = {"ok": True, "text": text, "code": code}
                if images:
                    payload["images"] = images
                if conv_state:
                    payload["conversation"] = conv_state
                out_path.write_text(json.dumps(payload, ensure_ascii=False),
                                    encoding="utf-8")
            else:
                out_path.write_text(text, encoding="utf-8")
            pointer = {"ok": True, "f": self._short_path(out_path),
                       "s": out_path.stat().st_size, "b": len(code)}
            if conv_state:
                pointer["c"] = conv_state.get("cid")
                pointer["t"] = conv_state.get("turns")
            print(json.dumps(pointer, ensure_ascii=False))

        elif args.json:
            payload = {"ok": True, "text": text, "code": code}
            if images:
                payload["images"] = images
            if conv_state:
                payload["conversation"] = conv_state
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(text)

    @staticmethod
    def _short_path(p: Path) -> str:
        """Return ./relative/path when under cwd, absolute otherwise. Saves bytes."""
        try:
            rel = p.resolve().relative_to(Path.cwd())
            return "./" + str(rel).replace("\\", "/")
        except ValueError:
            return str(p.resolve())

    # ── main ──────────────────────────────────────────────

    async def run(self):
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')

        parser = argparse.ArgumentParser(
            description="AI-native CLI for Gemini — flexible multimodal input via browser session",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""Examples:
  python gemini.py "Explain quantum computing"
  python gemini.py -i chart.png "What trend does this show?"
  python gemini.py -i a.jpg -i b.jpg "Compare these"
  python gemini.py -f report.pdf "Summarize this document"
  echo "Hello in French" | python gemini.py
  python gemini.py -i ui.png --brief -o review.md -q
  python gemini.py -l  "Auto-login via browser"

Multi-turn conversations:
  python gemini.py -c chat.json "My favorite color is blue."
  python gemini.py -c chat.json "What color did I say was my favorite?"
  python gemini.py -c chat.json --new "Start a different topic"

Model selection (auto-discovered at runtime, no hardcoded names):
  python gemini.py --list-models
  python gemini.py "fast answer" -m flash
  python gemini.py -i complex.png "deep analysis" -m pro""")
        parser.add_argument("prompt", nargs="*", type=str,
            help="Prompt text (concatenated with spaces). Reads from stdin if empty.")
        parser.add_argument("-p", "--prompt-text", type=str, dest="prompt_str",
            help="Prompt text (alternative to positional)")
        parser.add_argument("-i", "--image", type=str, action="append", dest="images",
            default=[], metavar="FILE", help="Attach an image file (repeatable)")
        parser.add_argument("-f", "--file", type=str, action="append", dest="files",
            default=[], metavar="FILE", help="Attach a document — PDF, text, CSV, etc. (repeatable)")
        parser.add_argument("-c", "--conversation", type=str, metavar="FILE",
            help="Conversation state file for multi-turn chats")
        parser.add_argument("--new", action="store_true", dest="new_conv",
            help="Start a new conversation even if -c FILE already exists")
        parser.add_argument("-m", "--model", type=str, metavar="MODEL",
            help="Model to use: 'flash', 'pro', 'lite', or a full model ID. Auto-discovered at runtime.")
        parser.add_argument("--thinking", type=str, metavar="TIER",
            choices=["standard", "plus", "extended"],
            help="Thinking level: standard (default), plus, extended. [experimental: may not differ yet via web API]")
        parser.add_argument("--list-models", action="store_true",
            help="Print available models and exit")
        parser.add_argument("-o", "--output", type=str, metavar="FILE",
            help="Write response to FILE instead of stdout (stdout gets a pointer JSON)")
        parser.add_argument("--json", action="store_true",
            help="Structured JSON for agent consumption")
        parser.add_argument("--brief", action="store_true",
            help="Prepend 'Be concise.' to the prompt for shorter responses")
        parser.add_argument("-g", "--gem", type=str, metavar="GEM",
            help="Gem ID or name to use as system prompt")
        parser.add_argument("--list-gems", action="store_true",
            help="Fetch and list available Gems, then exit")
        parser.add_argument("--setup-search-gem", action="store_true",
            help="Create or update the 'Gemini search' Gem with optimized search grounding prompt, then exit")
        parser.add_argument("-l", "--login", action="store_true",
            help="Open browser to sign into gemini.google.com and auto-capture cookies")
        parser.add_argument("--browser", type=str, metavar="BROWSER",
            choices=["chrome", "firefox", "edge", "safari"],
            help="Preferred browser for cookie extraction (default: platform-specific). "
                 "Also reads GEMINI_BROWSER env var. WSL/Linux defaults to chrome; "
                 "set --browser firefox to use Firefox cookies.")
        parser.add_argument("-q", "--quiet", action="store_true",
            help="Suppress progress messages on stderr")
        parser.add_argument("--no-retry", action="store_true",
            help="Disable automatic cookie refresh and retry")
        args = parser.parse_args()

        # Auto-quiet: when stdout is captured by an agent (pipe, subprocess), suppress logs
        if not args.quiet and not sys.stdout.isatty():
            args.quiet = True
        self.quiet = args.quiet

        # ── Build prompt ──
        if args.prompt_str:
            prompt = args.prompt_str
        elif args.prompt:
            prompt = " ".join(args.prompt)
        elif args.list_models:
            prompt = ""  # no prompt needed
        else:
            if not sys.stdin.isatty():
                prompt = sys.stdin.read().strip()
                if not prompt:
                    self.fail("NO_PROMPT", "No prompt provided. Use positional args, -p, or pipe text via stdin.")
            else:
                self.fail("NO_PROMPT", "No prompt provided. Use positional args, -p, or pipe text via stdin.")

        if args.brief and not prompt.startswith("Be concise"):
            prompt = "Be concise. " + prompt

        # ── Conversation state ──
        conv_state = None
        chat_metadata = None

        if args.conversation:
            if not args.new_conv:
                conv_state = self.load_conversation(args.conversation)
                if conv_state:
                    chat_metadata = conv_state.get("metadata")
                    self.log(f"Continuing conversation {conv_state['cid']} (turn {conv_state.get('turns', 0) + 1})")

            if conv_state is None:
                conv_state = {
                    "cid": None,
                    "metadata": None,
                    "turns": 0,
                    "created": datetime.now(timezone.utc).isoformat(),
                }
                self.log("Starting new conversation")

        # ── Collect files ──
        all_files = []
        for img in args.images:
            p = Path(img)
            if not p.exists():
                self.fail("FILE_NOT_FOUND", f"Image not found: {img}")
            all_files.append(str(p))
        for f in args.files:
            p = Path(f)
            if not p.exists():
                self.fail("FILE_NOT_FOUND", f"File not found: {f}")
            all_files.append(str(p))

        if all_files:
            self.log(f"{len(all_files)} attachment(s): {', '.join(Path(f).name for f in all_files)}")

        # ── Auth ──
        sid = os.getenv("GEMINI_SID")
        ts = os.getenv("GEMINI_TS")
        self._browser_pref = args.browser or os.getenv("GEMINI_BROWSER")
        cookie_source = "env" if sid else "browser"
        if not sid:
            sid, ts = self.extract_cookies(preferred=self._browser_pref)
        if not sid:
            if args.login:
                sid, ts = self._browser_login_flow("browser")
            else:
                self.log("No cookies found. Run with --login to open browser login.")
                self.fail("AUTH_EXPIRED",
                    "No Gemini cookies. Use --login to sign in via browser, or set GEMINI_SID/GEMINI_TS env vars.")

        # ── Model / Gem (init client early so resolve_model can query live list) ──
        need_client = args.model or args.list_models or args.gem or args.list_gems or args.setup_search_gem
        if need_client:
            try:
                self.client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await self.client.init()
            except Exception as e:
                self.fail("CLIENT_INIT_FAILED", str(e))

        if args.list_models:
            models = self.client.list_models()
            print(json.dumps({"ok": True, "models": [str(m) for m in models]},
                             ensure_ascii=False))
            return

        if args.list_gems:
            try:
                gems_text = await self.fetch_and_list_gems()
            except Exception as e:
                self.fail("GEM_FETCH_FAILED", str(e))
            print(gems_text)
            return

        if args.setup_search_gem:
            try:
                gem_id = await self.setup_search_gem()
                print(json.dumps({"ok": True, "action": "setup_search_gem",
                                  "gem_id": gem_id, "gem_name": SEARCH_GEM_NAME},
                                 ensure_ascii=False))
            except Exception as e:
                self.fail("GEM_SETUP_FAILED", str(e))
            return

        # Resolve gem
        gem_id = None
        if args.gem:
            try:
                await self.client.fetch_gems()
                gem_id = self.resolve_gem(args.gem)
                self.log(f"Gem: {args.gem}" + (f" -> {gem_id}" if gem_id != args.gem else ""))
            except Exception as e:
                self.fail("GEM_FETCH_FAILED", str(e))

        model = self.resolve_model(args.model, args.thinking)
        if model:
            label = model.name if hasattr(model, 'name') else model
            tier = f" ({args.thinking})" if args.thinking else ""
            self.log(f"Model: {label}{tier}")

        # ── Generate with retry ──
        max_rounds = 3 if not args.no_retry else 1

        for attempt in range(max_rounds):
            self.log(f"Attempt {attempt + 1}/{max_rounds}...")

            ok, result, new_metadata, images = await self.generate(
                sid, ts, prompt, all_files, chat_metadata, model, gem_id)

            if ok:
                # Update conversation state from response metadata
                if args.conversation and new_metadata:
                    conv_state["cid"] = new_metadata[0]
                    conv_state["metadata"] = new_metadata
                    conv_state["turns"] += 1
                    self.save_conversation(args.conversation, conv_state)

                self.emit(result, args, conv_state if args.conversation else None, images)
                return

            if not self.is_auth_error(result):
                self.fail("REQUEST_FAILED", result)

            self.log(f"Auth expired ({result[:80]}...)")

            if attempt == max_rounds - 1:
                break

            # Re-scan cookies first (may have been refreshed in another window)
            self.log("Re-scanning browser cookies...")
            new_sid, new_ts = self.extract_cookies(preferred=self._browser_pref)
            if new_sid and new_sid != sid:
                sid, ts = new_sid, new_ts
                cookie_source = "browser"
                self.client = None
                self.log("Found fresher cookies, retrying...")
                continue

            # Open browser for re-auth
            new_sid, new_ts = self._browser_login_flow(cookie_source)
            if new_sid:
                sid, ts = new_sid, new_ts
                cookie_source = "browser"
                self.client = None
            else:
                break

        self.fail("AUTH_EXPIRED",
            "Gemini session expired. Re-login at gemini.google.com and retry.")


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    cli = GeminiCLI()
    asyncio.run(cli.run())
