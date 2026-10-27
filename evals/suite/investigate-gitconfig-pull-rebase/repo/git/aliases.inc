[alias]
	st = status -sb
	co = checkout
	lg = log --graph --oneline --decorate -20
	up = pull --rebase=merges --autostash
	amend = commit --amend --no-edit
[pull]
	ff = only
