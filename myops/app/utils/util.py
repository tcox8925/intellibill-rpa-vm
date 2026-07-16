from fastapi import Request
import ipaddress

def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # sometimes contains a list: "client, proxy1, proxy2"
        ip = xff.split(",")[0].strip()
    else:
        client = request.client
        ip = client.host if client else "unknown"

    # remove port if present (e.g., "127.0.0.1:54321")
    if ":" in ip and not ipaddress.ip_address(ip.split(":")[0]):
        ip = ip.split(":")[0]

    return ip

def build_project_tree(items, permissions=None):
    """
    items: list of nav items
    permissions: {
        "my_ops": [3, 4],
        "my_admin": [47, 71]
    } or None
    """

    # If permissions is None, treat as empty permissions
    permissions = permissions or {}

    # Convert permission lists to sets for fast lookup
    permission_sets = {
        "my_ops": set(permissions.get("my_ops", [])),
        "my_admin": set(permissions.get("my_admin", []))
    }

    # Root containers
    roots = {
        "my_ops": [],
        "my_admin": []
    }

    # Initialize children list & default selected = False
    for item in items:
        item.selected = False
        item.children = []

    # Build parent lookup
    lookup = {item.id: item for item in items}

    # Build tree structure
    for item in items:

        # Set selected = True if present in permissions
        if item.id in permission_sets.get(item.project, set()):
            item.selected = True

        # Skip global root nodes
        if item.id in (1, 2):
            continue

        # Root-level nodes (e.g., DASH_)
        if item.nav_abbr.startswith("DASH_"):
            roots[item.project].append(item)
            continue

        # Attach to parent
        parent = lookup.get(item.parent_id)
        if parent:
            parent.children.append(item)

    # Recursive sorting
    def sort_tree(node):
        node.children.sort(key=lambda x: x.order_num)
        for child in node.children:
            sort_tree(child)

    # Sort roots + children
    for project in roots:
        roots[project].sort(key=lambda x: x.order_num)
        for root in roots[project]:
            sort_tree(root)

    return roots
