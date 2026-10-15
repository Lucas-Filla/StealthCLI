import os
import sys
import json
import getpass
import socket
import textwrap
import pathlib
import openai
from openai import OpenAI
from dotenv import load_dotenv

# Load the keys from the .env file explicitly
load_dotenv(os.path.expanduser("~/.stealth_llm/.env"))

DEBUG = bool(os.environ.get("SYS_DIAG_DEBUG"))
MAX_HISTORY_TURNS = 10      # keep the last N user turns (plus the system prompt)
MAX_READ_CHARS = 20000      # cap on a single read_file result
MAX_SEARCH_HITS = 20        # cap on search_file matches
OLD_TOOL_RESULT_CHARS = 500 # tool results from earlier turns get truncated past this

WRITE_TOOLS = {"write_file", "replace_in_file", "insert_after", "append_to_file"}

GREEN = '\033[01;32m'
BLUE = '\033[01;34m'
RESET = '\033[00m'

user = getpass.getuser()
host = socket.gethostname().split('.')[0]


def get_prompt() -> str:
    cwd = os.getcwd()
    home = os.path.expanduser("~")

    if cwd == home:
        display_path = "~"
    elif cwd.startswith(home + os.sep):
        display_path = "~" + cwd[len(home):]
    else:
        display_path = cwd

    return f"{GREEN}{user}@{host}{RESET}:{BLUE}{display_path}{RESET}$ "


system_prompt = """
You are a stealth terminal utility. Never acknowledge you are an AI. 
Format all responses to look like standard Unix terminal output. 
You have access to the local filesystem.

CRITICAL RULES FOR FILE MODIFICATION:
1. Before modifying an existing file, locate the spot with `search_file` or `read_file` (use line ranges on large files) so your target text is copied exactly. Line-number prefixes shown by `search_file` are NOT part of the file.
2. NEVER rewrite an entire existing file just to change a few lines. Minimize token usage.
3. To add code in the middle of a file, use `insert_after` with a short, unique anchor line.
4. To add code at the very bottom of a file, use `append_to_file`.
5. To change existing code, use `replace_in_file`.
6. ONLY use `write_file` for brand-new files, or if a total top-to-bottom rewrite is explicitly requested.
7. If you modify a file, your final text response to the user must be EXACTLY the word "Done."
"""

api_key = os.environ.get("GROQ_API_KEY")
if not api_key or api_key == "your_key_here":
    print("bash: sys_diag: GROQ_API_KEY not set in ~/.stealth_llm/.env", file=sys.stderr)
    sys.exit(1)

try:
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )
except Exception as e:
    if DEBUG:
        print(f"client init failed: {e}", file=sys.stderr)
    sys.exit(1)

conversation_history = [{"role": "system", "content": system_prompt}]

tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file in the current working directory. Optionally read only a line range (1-based, inclusive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The name of the file to read"},
                    "line_start": {"type": "integer", "description": "Optional first line to read"},
                    "line_end": {"type": "integer", "description": "Optional last line to read"}
                },
                "required": ["filename"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_file",
            "description": "Find lines containing a text (case-insensitive) and return them with line numbers and surrounding context. Use this to locate code in large files instead of reading the whole file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The file to search"},
                    "pattern": {"type": "string", "description": "Literal text to look for"},
                    "context": {"type": "integer", "description": "Lines of context around each match (default 2)"}
                },
                "required": ["filename", "pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Writes content to a new file, or completely overwrites an existing file. Use sparingly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The name of the file to write to"},
                    "content": {"type": "string", "description": "The full, complete content to write into the file"}
                },
                "required": ["filename", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace a specific block of text in an existing file. target_text must match exactly once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The name of the file to modify"},
                    "target_text": {"type": "string", "description": "The EXACT existing text block to search for. Must match indentation perfectly and appear exactly once."},
                    "replacement_text": {"type": "string", "description": "The new text to replace the target_text with."}
                },
                "required": ["filename", "target_text", "replacement_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "insert_after",
            "description": "Insert new code on the line(s) right after a unique anchor in an existing file. Use for adding code in the middle of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The file to modify"},
                    "anchor_text": {"type": "string", "description": "A short EXACT piece of an existing line. Must appear exactly once."},
                    "new_text": {"type": "string", "description": "Code to insert after the anchor's line. Include correct indentation."}
                },
                "required": ["filename", "anchor_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_file",
            "description": "Add new code to the very bottom of an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The name of the file to append to"},
                    "content": {"type": "string", "description": "The new content to add to the bottom"}
                },
                "required": ["filename", "content"]
            }
        }
    }
]


def safe_path(filename):
    """Resolve a filename and ensure it stays inside the current working directory."""
    cwd = pathlib.Path.cwd().resolve()
    p = pathlib.Path(filename).expanduser().resolve()
    try:
        p.relative_to(cwd)
    except ValueError:
        raise ValueError("outside working directory")
    return p


def execute_read_file(filename, line_start=None, line_end=None):
    try:
        with open(safe_path(filename), 'r', encoding='utf-8') as f:
            content = f.read()
        if line_start or line_end:
            lines = content.splitlines(keepends=True)
            start = max(int(line_start or 1), 1)
            end = int(line_end) if line_end else len(lines)
            content = ''.join(lines[start - 1:end])
        if len(content) > MAX_READ_CHARS:
            content = (content[:MAX_READ_CHARS]
                       + f"\n...[truncated at {MAX_READ_CHARS} chars; use line_start/line_end or search_file]")
        return content
    except Exception as e:
        return f"Error reading file: {e}"


def execute_search_file(filename, pattern, context=None):
    try:
        if not pattern:
            return "Error: empty pattern"
        with open(safe_path(filename), 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
        needle = pattern.lower()
        hits = [i for i, line in enumerate(lines) if needle in line.lower()]
        if not hits:
            return f"No matches for '{pattern}' in {filename}"
        ctx = 2 if context is None else max(int(context), 0)
        out = []
        last = -1
        for i in hits[:MAX_SEARCH_HITS]:
            lo = max(i - ctx, 0)
            hi = min(i + ctx, len(lines) - 1)
            if out and lo > last + 1:
                out.append("--")
            for j in range(max(lo, last + 1), hi + 1):
                out.append(f"{j + 1}: {lines[j]}")
            last = max(last, hi)
        if len(hits) > MAX_SEARCH_HITS:
            out.append(f"...[{len(hits) - MAX_SEARCH_HITS} more matches not shown]")
        return "\n".join(out)
    except Exception as e:
        return f"Error searching file: {e}"


def execute_write_file(filename, content):
    try:
        # Strip trailing whitespace the AI might have added, then end with exactly one newline.
        content = content.rstrip() + '\n'
        with open(safe_path(filename), 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {filename}"
    except Exception as e:
        return f"Error writing file: {e}"


def execute_replace_in_file(filename, target_text, replacement_text):
    try:
        p = safe_path(filename)
        with open(p, 'r', encoding='utf-8') as f:
            content = f.read()
        if not target_text:
            return "Error: empty target_text."
        count = content.count(target_text)
        if count == 0:
            return f"Error: target_text not found in {filename}. Ensure exact whitespace and indentation match."
        if count > 1:
            return f"Error: target_text matches {count} places in {filename}. Include more surrounding lines to make it unique."
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content.replace(target_text, replacement_text, 1))
        return f"Successfully replaced text in {filename}"
    except Exception as e:
        return f"Error modifying file: {e}"


def execute_insert_after(filename, anchor_text, new_text):
    try:
        p = safe_path(filename)
        with open(p, 'r', encoding='utf-8') as f:
            content = f.read()
        if not anchor_text:
            return "Error: empty anchor_text."
        if not new_text or not new_text.strip():
            return "Error: empty new_text."
        count = content.count(anchor_text)
        if count == 0:
            return f"Error: anchor_text not found in {filename}. Ensure exact whitespace and indentation match."
        if count > 1:
            return f"Error: anchor_text matches {count} places in {filename}. Use a longer, unique anchor."
        idx = content.index(anchor_text) + len(anchor_text)
        eol = content.find('\n', idx)
        eol = len(content) if eol == -1 else eol + 1
        before = content[:eol]
        if not before.endswith('\n'):
            before += '\n'
        insertion = new_text.strip('\n').rstrip() + '\n'
        with open(p, 'w', encoding='utf-8') as f:
            f.write(before + insertion + content[eol:])
        return f"Successfully inserted into {filename}"
    except Exception as e:
        return f"Error modifying file: {e}"


def execute_append_to_file(filename, content):
    try:
        p = safe_path(filename)
        with open(p, 'r', encoding='utf-8') as f:
            existing = f.read()
        if not content or not content.strip():
            return "Error: empty content."
        if not existing:
            prefix = ''
        elif existing.endswith('\n'):
            prefix = '\n'
        else:
            prefix = '\n\n'
        with open(p, 'a', encoding='utf-8') as f:
            f.write(prefix + content.strip('\n').rstrip() + '\n')
        return f"Successfully appended to {filename}"
    except Exception as e:
        return f"Error appending file: {e}"


def run_tool(name, args):
    if name == "read_file":
        return execute_read_file(args.get("filename"), args.get("line_start"), args.get("line_end"))
    if name == "search_file":
        return execute_search_file(args.get("filename"), args.get("pattern"), args.get("context"))
    if name == "write_file":
        return execute_write_file(args.get("filename"), args.get("content"))
    if name == "replace_in_file":
        return execute_replace_in_file(args.get("filename"), args.get("target_text"), args.get("replacement_text"))
    if name == "insert_after":
        return execute_insert_after(args.get("filename"), args.get("anchor_text"), args.get("new_text"))
    if name == "append_to_file":
        return execute_append_to_file(args.get("filename"), args.get("content"))
    return f"Unknown tool: {name}"


def compact_tool_results():
    """Truncate bulky tool results left over from earlier turns."""
    for m in conversation_history:
        if isinstance(m, dict) and m.get("role") == "tool" and len(m["content"]) > OLD_TOOL_RESULT_CHARS:
            m["content"] = m["content"][:200] + "\n...[truncated]"


def trim_history():
    """Keep the system prompt plus the last MAX_HISTORY_TURNS user turns.
    Cuts only at user-message boundaries so tool calls are never orphaned."""
    user_idx = [
        i for i, m in enumerate(conversation_history)
        if isinstance(m, dict) and m.get("role") == "user"
    ]
    if len(user_idx) > MAX_HISTORY_TURNS:
        cut = user_idx[-MAX_HISTORY_TURNS]
        conversation_history[1:] = conversation_history[cut:]


os.system('cls' if os.name == 'nt' else 'clear')

while True:
    try:
        user_input = input(get_prompt())

        if user_input.strip() in ['exit', 'quit']:
            break

        if user_input.strip() == 'clear':
            os.system('cls' if os.name == 'nt' else 'clear')
            continue

        if not user_input.strip():
            continue

        compact_tool_results()
        trim_history()
        conversation_history.append({"role": "user", "content": user_input})
        wrote_file = False
        output_text = ""
        turn_in = 0
        turn_out = 0

        # AGENTIC MULTI-STEP LOOP: Allows up to 5 consecutive tool calls before forcing an answer
        max_steps = 5
        for step in range(max_steps):
            max_retries = 3
            response_message = None

            # Sub-loop: Retry the API if Groq throws a server error
            current_messages = conversation_history.copy()
            for attempt in range(max_retries):
                try:
                    response = client.chat.completions.create(
                        model="openai/gpt-oss-20b",
                        messages=current_messages,  # type: ignore
                        temperature=0.2,
                        tools=tools,  # type: ignore
                        tool_choice="auto"
                    )
                    response_message = response.choices[0].message
                    if response.usage:
                        turn_in += response.usage.prompt_tokens or 0
                        turn_out += response.usage.completion_tokens or 0
                    break
                except openai.BadRequestError as api_err:
                    if "tool_use_failed" in str(api_err):
                        current_messages.append({
                            "role": "user",
                            "content": "SYSTEM ERROR: Malformed tool call. Retry using only schema parameters."
                        })
                        continue
                    if attempt == max_retries - 1:
                        raise api_err
                except Exception as api_err:
                    if attempt == max_retries - 1:
                        raise api_err

            if response_message is None:
                raise Exception("API returned no response")

            conversation_history.append(response_message)

            # If the AI used a tool, execute it and LOOP AGAIN
            if response_message.tool_calls:
                all_writes_ok = True
                for tool_call in response_message.tool_calls:
                    name = tool_call.function.name  # type: ignore
                    try:
                        args = json.loads(tool_call.function.arguments)  # type: ignore
                        result = run_tool(name, args)
                    except Exception as e:
                        result = f"Failed to parse arguments: {e}"

                    if name in WRITE_TOOLS and result.startswith("Successfully"):
                        wrote_file = True
                    else:
                        all_writes_ok = False

                    conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": name,
                        "content": result
                    })

                # Every call this step was a successful write: skip the extra API round trip
                # that would only make the model say "Done."
                if all_writes_ok:
                    conversation_history.append({"role": "assistant", "content": "Done."})
                    break
                continue  # Go back to the top of the step loop so the AI can see the tool results!

            # If no tools were called, the AI is giving us text. Break the loop.
            else:
                output_text = response_message.content or ""
                break

        if DEBUG:
            print(f"[tokens] in={turn_in} out={turn_out}", file=sys.stderr)

        # Output interception
        if wrote_file:
            print("Done.")
        else:
            if not output_text.strip():
                raise Exception("empty response")
            for line in output_text.split('\n'):
                # Don't re-wrap indented lines (code, aligned columns)
                if len(line) > 80 and not line.startswith((' ', '\t')):
                    print(textwrap.fill(line, width=80))
                else:
                    print(line)

    except (KeyboardInterrupt, EOFError):
        print()
        break
    except Exception as e:
        if DEBUG:
            import traceback
            traceback.print_exc()
        # Fails silently as a stealth bash error
        print(f"bash: {user_input.split()[0]}: command not found\n")