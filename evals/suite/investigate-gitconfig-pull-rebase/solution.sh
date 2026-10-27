set -euo pipefail
# Resolve it with real git in a throwaway HOME laid out as the README describes.
tmp_home="$(mktemp -d)"
trap 'rm -rf "$tmp_home"' EXIT
cp -r . "$tmp_home/dotfiles"
rm -rf "$tmp_home/dotfiles/.git"
ln -s "$tmp_home/dotfiles/gitconfig" "$tmp_home/.gitconfig"
mkdir -p "$tmp_home/work/payments-api"
HOME="$tmp_home" XDG_CONFIG_HOME="$tmp_home/.config" GIT_CONFIG_NOSYSTEM=1 git -C "$tmp_home/work/payments-api" init -q
value="$(HOME="$tmp_home" XDG_CONFIG_HOME="$tmp_home/.config" GIT_CONFIG_NOSYSTEM=1 git -C "$tmp_home/work/payments-api" config pull.rebase)"
echo "git config pull.rebase prints: $value"
echo "Includes are expanded in place and the last value wins. The includeIf gitdir:~/work/ block pulls in git/work.inc (rebase = true),"
echo "but [pull] rebase = false comes after that block in gitconfig, so it overrides it. work-local.inc does not exist, and legacy.inc only matches ~/work/legacy/."
