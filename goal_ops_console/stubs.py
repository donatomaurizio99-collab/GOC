class QdrantClientStub:
    def search(self, *args, **kwargs):
        return []


class PermissionManager:
    def check(self, category, key):
        return True
