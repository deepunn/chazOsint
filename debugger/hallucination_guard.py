class HallucinationGuard:
    """Yanıltıcı veya hatalı verileri temizleyen filtre"""
    def __init__(self):
        self.blacklisted_terms = ["not found", "error 404", "hesap bulunamadı", "silinmiş"]

    def sanitize(self, text_list: list) -> list:
        return [text for text in text_list if not any(term in text.lower() for term in self.blacklisted_terms)]