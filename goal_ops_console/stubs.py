class QdrantClientStub:
    def search(self, *args, **kwargs):
        return []


class Planner:
    def create_plan(self, goal):
        return {"steps": [], "stub": True, "goal_id": goal["goal_id"]}


class PermissionManager:
    def check(self, category, key):
        return True
