Créé : tools/image/qrcode_reader.py — QRCodeToURLTool

Utilise pyzbar (wrapper de la lib C zbar) — détecte les QR codes peu importe leur position, taille ou rotation dans l'image
Pillow pour le chargement de l'image
100% local, aucun service web ni LLM
Si plusieurs QR codes sont présents, tous sont retournés (le premier dans url, la liste complète dans qrcodes)
Retourne aussi la position (bounding box) de chaque QR code détecté
Note : pyzbar nécessite la lib système zbar :


# macOS
brew install zbar

# Debian/Ubuntu
sudo apt-get install libzbar0
Usage :


tool = QRCodeToURLTool()
result = tool.run_from_dict({"image_path": "/tmp/photo.png"})
print(result.data["url"])  # https://example.com
