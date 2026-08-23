import os
cfg = open('config.py', encoding='utf-8').read()
if 'GEMINI_API_KEY_BACKUP' not in cfg:
    add = '\n\n# GEMINI BACKUP API KEY\nGEMINI_API_KEY_BACKUP = os.getenv("GEMINI_API_KEY_BACKUP", "").strip()\nif not GEMINI_API_KEY_BACKUP:\n    p = os.path.join(BASE_DIR, "api_backup.txt")\n    if os.path.exists(p):\n        try:\n            with open(p, "r", encoding="utf-8") as f:\n                GEMINI_API_KEY_BACKUP = f.read().strip()\n        except Exception:\n            pass\n'
    cfg = cfg.rstrip() + '\n' + add
    open('config.py', 'w', encoding='utf-8').write(cfg)
    print('CONFIG_BACKUP_ADDED')
else:
    print('CONFIG_BACKUP_EXISTS')