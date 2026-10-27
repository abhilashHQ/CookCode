set -euo pipefail
cat > .gitattributes <<'EOF'
* text=auto
*.sh text eol=lf
*.bat text eol=crlf
*.cmd text eol=crlf
*.png binary
*.jpg binary
EOF
