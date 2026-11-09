"""Review decisions for assets retained by a draft."""


def media_state(item, asset):
    default = "pending" if "editorial" in item and not asset.get("local") else "approved"
    return item.get("media_review", {}).get(asset["page_url"], default)


def usable_media(item):
    return [asset for asset in item.get("media", []) if media_state(item, asset) == "approved"]
