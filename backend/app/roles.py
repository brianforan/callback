"""Who may administer Callback: users with the admin role, and nobody else.

No setting makes anyone an admin. The first admin is made on the server with
`python -m app.cli set-role --email you@example.com --role admin`, or in the database.
"""

from app.models import Role, User


def is_admin_role(role: str) -> bool:
    return role == Role.ADMIN


def is_admin(user: User) -> bool:
    return is_admin_role(user.role)
