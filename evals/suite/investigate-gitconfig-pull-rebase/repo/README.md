# dotfiles

```
git clone git@github.com:priya-raman/dotfiles.git ~/dotfiles
ln -sf ~/dotfiles/gitconfig ~/.gitconfig
```

Git settings are split up:

- `gitconfig` - the main file, symlinked to `~/.gitconfig`
- `git/aliases.inc` - aliases, shared everywhere
- `git/work.inc` - applied to repositories under `~/work/`
- `git/legacy.inc` - the old monorepo under `~/work/legacy/` still needs merge pulls
- `git/work-local.inc` - per-machine overrides for work repos, edited by hand
