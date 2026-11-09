# Mario'nun belge klasörü

Mario'nun hatırlamasını istediğin belgeleri buraya koy: PDF, Markdown (`.md`) ya da düz metin (`.txt`).
Alt klasörler de taranır; adı `.` ya da `_` ile başlayan klasörler atlanır. Sonra Mario'ya
"belgelerimi tara" de (`index_documents`). Yalnızca yeni ve değişen dosyalar yeniden işlenir.

Bu klasörün içeriği kişiseldir ve git'e girmez (bu README hariç). Başka bir klasör kullanmak için
`backend/.env` içinde `MEMORY_DOCS_DIR` ayarla.
