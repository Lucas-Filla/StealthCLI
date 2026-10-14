#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.stealth_llm"

echo "Installing StealthCLI to $INSTALL_DIR..."

# 1. Create the hidden directory
mkdir -p "$INSTALL_DIR"

# 2. Copy the main script
cp "$SCRIPT_DIR/sys_diag.py" "$INSTALL_DIR/"

# 3. Set up Python virtual environment
echo "Setting up Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"

# 4. Handle the .env file safely
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo "GROQ_API_KEY=your_key_here" > "$INSTALL_DIR/.env"
    echo "Created .env file. You MUST add your Groq API key to: $INSTALL_DIR/.env"
fi
chmod 600 "$INSTALL_DIR/.env"

# 5. Detect the user's shell profile
case "$SHELL" in
    *zsh*)
        SHELL_PROFILE="$HOME/.zshrc"
        ;;
    *bash*)
        # macOS Terminal opens bash as a login shell, which reads .bash_profile
        if [ "$(uname)" = "Darwin" ]; then
            SHELL_PROFILE="$HOME/.bash_profile"
        else
            SHELL_PROFILE="$HOME/.bashrc"
        fi
        ;;
    *)
        SHELL_PROFILE="$HOME/.profile"
        ;;
esac
touch "$SHELL_PROFILE"

# 6. Inject the alias (using the isolated venv Python)
ALIAS_CMD="alias sys_diag='$INSTALL_DIR/venv/bin/python $INSTALL_DIR/sys_diag.py'"

if ! grep -q "alias sys_diag=" "$SHELL_PROFILE"; then
    printf '\n# StealthCLI\n%s\n' "$ALIAS_CMD" >> "$SHELL_PROFILE"
    echo "Added 'sys_diag' alias to $SHELL_PROFILE."
else
    echo "Alias 'sys_diag' already exists in $SHELL_PROFILE."
fi

echo "Installation complete."
echo "Run: source $SHELL_PROFILE   (or restart your terminal), then: sys_diag"