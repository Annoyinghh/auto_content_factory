"""Review decisions for assets retained by a draft."""


def media_state(item, asset):
    if any(item.get("media_review", {}).get(source["page_url"]) == "removed"
           for source in asset.get("comparison", {}).get("sources", ())):
        return "removed"
    default = "pending" if "editorial" in item and not asset.get("local") else "approved"
    return item.get("media_review", {}).get(asset["page_url"], default)


def usable_media(item):
    return [asset for asset in item.get("media", []) if media_state(item, asset) == "approved"]
