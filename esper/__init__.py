# Lightweight esper compatibility shim for tests
# Provides minimal World and Processor to satisfy tests and server usage.
from __future__ import annotations
from typing import Any, Dict, List, Tuple, Type, Iterable


class Processor:
    def __init__(self) -> None:
        self.world: World | None = None

    def process(self) -> None:
        pass


class World:
    def __init__(self) -> None:
        self._next_eid: int = 1
        self._entities: Dict[int, List[Any]] = {}
        self._processors: List[Processor] = []

    # Esper API surface used in repo/tests
    def add_processor(self, processor: Processor) -> None:
        processor.world = self
        self._processors.append(processor)

    def create_entity(self, *components: Any) -> int:
        eid = self._next_eid
        self._next_eid += 1
        self._entities[eid] = list(components)
        return eid

    def add_component(self, eid: int, component: Any) -> None:
        comps = self._entities.setdefault(eid, [])
        comps.append(component)

    def remove_component(self, eid: int, component_type: Type[Any]) -> None:
        comps = self._entities.get(eid, [])
        for i, c in enumerate(list(comps)):
            if isinstance(c, component_type):
                del comps[i]
                return
        # if not found, no-op (compat with some esper versions)

    def get_components(self, *component_types: Type[Any]) -> Iterable[Tuple[int, Tuple[Any, ...]]]:
        # Iterate entities that have at least one instance of each requested type
        for eid, comps in self._entities.items():
            found: List[Any] = []
            ok = True
            for t in component_types:
                match = None
                for c in comps:
                    if isinstance(c, t):
                        match = c
                        break
                if match is None:
                    ok = False
                    break
                found.append(match)
            if ok:
                yield eid, tuple(found)  # type: ignore[return-value]

    def process(self) -> None:
        for p in list(self._processors):
            try:
                p.process()
            except Exception:
                # Keep test behavior robust; swallow to continue others
                raise

    def component_for_entity(self, eid: int, component_type: Type[Any]) -> Any:
        comps = self._entities.get(eid, [])
        for c in comps:
            if isinstance(c, component_type):
                return c
        raise KeyError(f"Entity {eid} does not have component {component_type}")


# Provide module-level fallbacks used in server for older patterns
def get_components(*args: Any, **kwargs: Any):
    # Not used when World is present; kept for compatibility
    raise NotImplementedError("Module-level get_components is not implemented in shim; use World().get_components")
