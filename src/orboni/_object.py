from __future__ import annotations

from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional


class OrboniObject(Dict[str, Any]):
    """An object the API answered: a dict whose fields also read as attributes.

        session["url"] == session.url

    Nested objects are OrboniObjects too, JSON arrays stay lists. A field
    that is missing raises KeyError with brackets and AttributeError as an
    attribute. A field named after a Python keyword (a transaction's
    ``from``) reads with brackets only.
    """

    __slots__ = ()

    def __init__(self, values: Optional[Mapping[str, Any]] = None, **fields: Any) -> None:
        super().__init__()
        for source in (values or {}, fields):
            for key, value in source.items():
                dict.__setitem__(self, key, convert_to_object(value))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"{type(self).__name__} has no field {name!r}") from None

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self[name] = value

    def __delattr__(self, name: str) -> None:
        if name.startswith("_"):
            object.__delattr__(self, name)
            return
        try:
            del self[name]
        except KeyError:
            raise AttributeError(f"{type(self).__name__} has no field {name!r}") from None

    def __dir__(self) -> List[str]:
        return sorted(set(super().__dir__()) | {key for key in self if isinstance(key, str) and key.isidentifier()})


class ListObject(OrboniObject):
    """One page of a list (``object``, ``data``, ``has_more``, ``url``), as the API answers it.

    ``auto_paging_iter()`` walks every object across the pages, asking for
    each next page as it goes.
    """

    __slots__ = ("_fetch", "_params")

    def __init__(self, values: Optional[Mapping[str, Any]] = None, **fields: Any) -> None:
        super().__init__(values, **fields)
        self._fetch: Optional[Callable[[Dict[str, Any]], ListObject]] = None
        self._params: Dict[str, Any] = {}

    def _attach(self, fetch: Callable[[Dict[str, Any]], ListObject], params: Dict[str, Any]) -> None:
        self._fetch = fetch
        self._params = dict(params)

    def __getstate__(self) -> None:
        # A copy or a pickle keeps the page, not the way to the next one.
        return None

    def auto_paging_iter(self) -> Iterator[OrboniObject]:
        """Every object, page after page, starting with this one.

        Forward by ``starting_after`` the last ID of each page while
        ``has_more``. When the list was asked with ``ending_before``,
        backwards by ``ending_before`` the first ID, each page's objects
        yielded oldest first. A list that is never paged (payment methods,
        currencies) yields its one page.
        """
        params: Dict[str, Any] = dict(getattr(self, "_params", None) or {})
        fetch = getattr(self, "_fetch", None)
        backwards = isinstance(params.get("ending_before"), str)
        page: OrboniObject = self
        while True:
            data = page.get("data")
            items: List[Any] = list(data) if isinstance(data, list) else []
            yield from (reversed(items) if backwards else items)
            edge = (items[0] if backwards else items[-1]) if items else None
            edge_id = edge.get("id") if isinstance(edge, Mapping) else None
            if fetch is None or page.get("has_more") is not True or not isinstance(edge_id, str):
                return
            params.pop("starting_after", None)
            params.pop("ending_before", None)
            params["ending_before" if backwards else "starting_after"] = edge_id
            page = fetch(dict(params))

    def auto_paging_to_list(self, *, limit: int) -> List[OrboniObject]:
        """Up to ``limit`` objects (1 to 10000) across the pages, in one list."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("auto_paging_to_list needs a limit between 1 and 10000.")
        if limit < 1 or limit > 10_000:
            raise ValueError("auto_paging_to_list needs a limit between 1 and 10000.")
        items: List[OrboniObject] = []
        for item in self.auto_paging_iter():
            items.append(item)
            if len(items) >= limit:
                break
        return items


def convert_to_object(value: Any) -> Any:
    """JSON as the API sent it, its objects made OrboniObjects (ListObjects for lists)."""
    if isinstance(value, OrboniObject):
        return value
    if isinstance(value, dict):
        return ListObject(value) if value.get("object") == "list" else OrboniObject(value)
    if isinstance(value, list):
        return [convert_to_object(item) for item in value]
    return value
