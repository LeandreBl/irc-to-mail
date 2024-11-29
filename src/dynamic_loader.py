import ultraimport


class DynamicLoader:
    def __init__(self):
        self.modules = {}

    def load(self, name, module_path: str):
        """
        Load a module dynamically from the given path.
        """
        existing = self.modules.get(module_path)
        if existing:
            return existing

        module = ultraimport(module_path, package=2)

        self.modules[name] = module

        return module
