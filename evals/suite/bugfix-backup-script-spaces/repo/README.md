# ops-scripts

Small shell helpers used on the office NAS.

## bin/backup.sh

Copies the top-level files of a folder into a timestamped snapshot directory
and prunes old snapshots.

```
bin/backup.sh ~/Documents/finance /mnt/backups/finance 7
```

Runs nightly from cron:

```
15 2 * * * /opt/ops-scripts/bin/backup.sh /srv/share/finance /mnt/backups/finance 14
```
