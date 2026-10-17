from concurrent.futures import ThreadPoolExecutor, as_completed

class OSINTThreadPool:
    """Asenkron görev yürütme ve çoklu iş parçacığı yöneticisi"""
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers

    def execute_tasks(self, task_list: list) -> list:
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {
                executor.submit(task['func'], *task.get('args', [])): task['name'] 
                for task in task_list
            }
            for future in as_completed(future_to_task):
                task_name = future_to_task[future]
                try:
                    data = future.result()
                    results.append({"task": task_name, "status": "SUCCESS", "data": data})
                except Exception as exc:
                    results.append({"task": task_name, "status": "FAILED", "error": str(exc)})
        return results