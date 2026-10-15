# StealthCLI

StealthCLI is a command-line AI assistant built to eliminate context-switching during development. Running under the alias sys_diag, it blends in perfectly as a native Linux/macOS background utility. Under the hood, it leverages Groq's high-speed API and an agentic tool-calling loop to autonomously read your codebase, debug issues, and apply changes directly to your local files. The interface strictly mimics a standard bash shell—meaning zero conversational fluff, no markdown rendering, and native-looking bash: command not found errors if the API drops.

## Features

- **Surgical File I/O:** Instead of completely rewriting files and burning tokens, the AI uses targeted tools (`search_file`, `insert_after`, `replace_in_file`, `append_to_file`) to locate and inject code with grep-like precision. It enforces strict matching to prevent accidental overwrites.
- **Token Efficiency:** Artificially short-circuits the API loop on successful writes to cut prompt token costs in half, and dynamically truncates old tool outputs to keep the context window lean.
* **Agentic Retry Loop:** Open-source models sometimes hallucinate tool arguments or format JSON poorly. The script catches `400` errors, secretly tells the AI to fix its formatting, and loops until it gets it right (up to 5 steps).
* **Path Traversal Protection:** Uses `pathlib` to ensure the LLM can only read/write files inside your current working directory. It can't accidentally (or maliciously) touch `~/.ssh` or `/etc/`.
* **Context Management:** Slices conversation history at user-message boundaries so long debugging sessions don't crash the API token limit, while keeping tool-call sequences intact.
* **Clean Install:** The `install.sh` script sets up an isolated Python `venv` so it doesn't pollute your global packages, and safely restricts `.env` permissions (`chmod 600`).

## Getting a Groq API Key

This tool uses Groq to keep the terminal responses feeling instantaneous. 
1. Go to the [GroqCloud Console](https://console.groq.com/).
2. Log in and go to **API Keys**.
3. Click **Create API Key** (it's free and they have a generous free tier). Save the `gsk_...` string.

## Installation

The install script sets up the hidden directory, builds the virtual environment, and adds the alias to your `.bashrc`, `.zshrc`, or `.bash_profile`.

1. **Clone the repo:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/StealthCLI.git
   cd StealthCLI
   ```

2. **Run the installer:**
   ```bash
   chmod +x install.sh
   ./install.sh
   ```

3. **Add your API Key:**
   Open the newly created config file and paste your Groq key:
   ```bash
   nano ~/.stealth_llm/.env
   ```

4. **Reload your shell:**
   ```bash
   source ~/.bashrc  # (or ~/.zshrc / ~/.bash_profile depending on your OS)
   ```

## Usage

Navigate to any code folder and type `sys_diag` to start the stealth session. 

```bash
# Example 1: Targeted debugging (Token Efficient)
lucas@Studio2:~/projects/my_app$ search main.py for the database connection
lucas@Studio2:~/projects/my_app$ insert the retry logic after the connection initialization
Done.

# Example 2: Creating new files from scratch
lucas@Studio2:~/projects/my_app$ create a new file called utils.py and write a python function to parse CSVs
Done.

lucas@Studio2:~/projects/my_app$ exit
```

**Commands:**
* `clear`: Clears the terminal natively.
* `exit` / `quit`: Exits the agent and returns to normal bash.
* `Ctrl+C` / `Ctrl+D`: Exits cleanly.
* `SYS_DIAG_DEBUG=1 sys_diag`: Runs the tool in debug mode to print token usage per turn and expose API stack traces.