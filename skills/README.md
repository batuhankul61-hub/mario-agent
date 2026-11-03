# Öğrenilmiş yetenekler

Mario bir isteği yapamadığında izin ister ve Claude Code'a öğretir. Claude, `.claude/skills/mario-yetenek`
skill'ine göre buraya bir klasör yazar: `skills/<ad>/skill.json` ve `run.ps1` ya da `run.py`.
Mario bu klasörü her çağrıda yeniden okur, bu yüzden yeni yetenek hemen kullanılabilir.

- Listelemek: Mario'ya "neler öğrendin?" de ya da `backend\.venv\Scripts\python backend\scripts\check_skill.py --list`.
- Unutturmak: "X yeteneğini unut" de; klasör `skills/_unutulan/` altına taşınır, silinmez.
- Bu klasördeki yetenekler kişiseldir ve git'e girmez (yalnızca bu README girer).
