class GraphBuilder:
    """Hedef varlık ile doğrulanmış hesaplar arasındaki ilişki ağını inşa eder."""

    def __init__(self, target_name: str, candidates: list):
        self.target_name = target_name
        self.candidates = candidates or []

    def build_node_network(self) -> dict:
        nodes = [{"id": self.target_name, "label": self.target_name, "group": "target"}]
        edges = []
        for item in self.candidates:
            if isinstance(item, dict):
                platform = item.get("platform", "?")
                url = item.get("url", "")
                handle = item.get("handle", "")
                prob = float(item.get("ai_prob", 0.0))
                node_id = f"{platform}:{handle or url}"
            else:  # geriye dönük tuple/str desteği
                if isinstance(item, tuple) and len(item) == 3:
                    platform, url, _ = item
                else:
                    platform, url = "Web Kaynağı", str(item)
                handle, prob = url, 0.5
                node_id = f"{platform}:{handle}"

            nodes.append({
                "id": node_id,
                "label": platform,
                "handle": handle,
                "url": url,
                "group": "social_account",
                "probability": prob,
            })
            edges.append({"from": self.target_name, "to": node_id, "relation": "owns", "weight": prob})
        return {"nodes": nodes, "edges": edges}
