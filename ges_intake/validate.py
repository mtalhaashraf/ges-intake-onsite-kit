def validate_library(library):
    """Validate the library for unknown references and dependency cycles.

    Raises ValueError if invalid.
    """
    cel_ids = {c["id"] for c in library["cel"]["cel"]}
    dt_ids = {dt["id"] for dt in library["document_types"]["document_types"]}

    # Check document types reference valid CELs
    for dt in library["document_types"]["document_types"]:
        if dt["cel"] not in cel_ids:
            raise ValueError(f"Document type {dt['id']} references unknown CEL {dt['cel']}")

    # Check services reference valid PDAs and CELs
    for service in library["services"]["services"]:
        pda_ids = {p["id"] for p in service["pdas"]}

        for pda in service["pdas"]:
            # Check depends_on references exist
            for dep in pda.get("depends_on", []):
                if dep not in pda_ids:
                    raise ValueError(f"PDA {pda['id']} depends on unknown PDA {dep}")

            # Check pathway requirements reference valid CELs
            for pathway in pda.get("pathways", []):
                for req in pathway.get("requirements", []):
                    if req["cel"] not in cel_ids:
                        raise ValueError(
                            f"PDA {pda['id']} pathway {pathway['name']} "
                            f"references unknown CEL {req['cel']}"
                        )

        # Check for dependency cycles using DFS
        _check_cycles(service["pdas"])


def _check_cycles(pdas):
    """Detect dependency cycles among PDAs using DFS."""
    graph = {p["id"]: p.get("depends_on", []) for p in pdas}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {pid: WHITE for pid in graph}

    def dfs(node):
        color[node] = GRAY
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                raise ValueError(f"Dependency cycle detected involving PDA {node} and {dep}")
            if color[dep] == WHITE:
                dfs(dep)
        color[node] = BLACK

    for pid in graph:
        if color[pid] == WHITE:
            dfs(pid)
