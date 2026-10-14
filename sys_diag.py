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
MAX_HISTORY_TURNS = 10  # keep the last N user turns (plus the system prompt)

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

CRITICAL RULES:
1. If the user asks you to "implement", "add", or "update" code in an existing file, you MUST use `read_file` first to analyze the existing content.
2. The `write_file` tool COMPLETELY OVERWRITES the file. When modifying an existing file, you must rewrite the ENTIRE file from top to bottom, inserting your new code into the best logical spot while strictly preserving all existing code.
3. ONLY use the `write_file` tool if the user explicitly asks to write, save, update, or implement in a file. Otherwise, print your answer to the console.
4. If you execute a `write_file` tool call, your final response back to the user must be EXACTLY the word "Done." with no other text.
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
            "description": "Read the contents of a file in the current working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The name of the file to read"},
                    "line_start": {"type": "integer", "description": "Ignored. Do not use."},
                    "line_end": {"type": "integer", "description": "Ignored. Do not use."}
                },
                "required": ["filename"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Writes content to a file. WARNING: This COMPLETELY OVERWRITES the file. If updating an existing file, you must provide the full file contents including the original code and your new changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The name of the file to write to"},
                    "content": {"type": "string", "description": "The full, complete content to write into the file"}
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


def execute_read_file(filename):
    try:
        with open(safe_path(filename), 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"


def execute_write_file(filename, content):
    try:
        with open(safe_path(filename), 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {filename}"
    except Exception as e:
        return f"Error writing file: {e}"


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

        trim_history()
        conversation_history.append({"role": "user", "content": user_input})
        wrote_file = False
        output_text = ""

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
                        messages=current_messages,
                        temperature=0.2,
                        tools=tools,
                        tool_choice="auto"
                    )
                    response_message = response.choices[0].message
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
                for tool_call in response_message.tool_calls:
                    try:
                        args = json.loads(tool_call.function.arguments)
                        if tool_call.function.name == "read_file":
                            result = execute_read_file(args.get("filename"))
                        elif tool_call.function.name == "write_file":
                            result = execute_write_file(args.get("filename"), args.get("content"))
                            if result.startswith("Successfully"):
                                wrote_file = True
                        else:
                            result = f"Unknown tool: {tool_call.function.name}"
                    except Exception as e:
                        result = f"Failed to parse arguments: {e}"

                    conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.function.name,
                        "content": result
                    })
                continue  # Go back to the top of the step loop so the AI can read the file data!

            # If no tools were called, the AI is giving us text. Break the loop.
            else:
                output_text = response_message.content or ""
                break

        # Output interception
        if wrote_file:
            print("Done.")
        else:
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