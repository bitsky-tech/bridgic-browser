
from pydantic import BaseModel


class PageSizeInfo(BaseModel):
    viewport_width: int
    viewport_height: int
    page_width: int
    page_height: int
    scroll_x: int
    scroll_y: int
    pixels_above: int
    pixels_below: int
    pixels_left: int
    pixels_right: int

class PageInfo(PageSizeInfo):
    url: str
    title: str

class FullPageInfo(PageInfo):
    tree: str

class PageDesc(BaseModel):
    url: str
    title: str
    page_id: str
