from fastapi import Request


def flash(request: Request, message: str, category: str = "success") -> None:
    messages = request.session.get("_flashes", [])
    messages.append({"message": message, "category": category})
    request.session["_flashes"] = messages


def get_flashed_messages(request: Request):
    messages = request.session.pop("_flashes", [])
    return messages
