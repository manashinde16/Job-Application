"""
Minimal PDF writer for ATS-parseable resumes. Pure stdlib.

Applicant tracking systems read the text layer of a PDF. They cope badly with
multi-column layouts, text inside images, and tables. So this produces the thing
that parses most reliably: a single column of real text in the base-14 fonts,
laid out top to bottom in reading order.

No third-party dependency, so the daily agent stays install-free.

    from resume import Document
    doc = Document()
    doc.name("Ananya Saini")
    doc.contact(["Nagpur, India", "+91-...", "email", "portfolio"])
    doc.section("EXPERIENCE")
    doc.job("Ascent Business Solutions", "Graphic Designer", "Jul 2025 - Present")
    doc.bullet("Created 100+ digital and print creatives ...")
    doc.save("out/resume.pdf")
"""

import zlib

# Base-14 metrics, units per 1000 at 1pt. Needed for real line wrapping — without
# them text either overflows the margin or wraps far too early.
_HELV = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 "
    "556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556 "
    "1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 "
    "667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 "
    "333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 "
    "556 556 333 500 278 556 500 722 500 500 500 334 260 334 584"
)
_HELV_BOLD = (
    "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 "
    "556 556 556 556 556 556 556 556 556 556 333 333 584 584 584 611 "
    "975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 "
    "667 778 722 667 611 722 667 944 667 667 611 333 278 333 584 556 "
    "333 556 611 556 611 556 333 611 611 278 278 556 278 889 611 611 "
    "611 611 389 556 333 611 556 778 556 556 500 389 280 389 584"
)

WIDTHS = {
    "F1": [int(w) for w in _HELV.split()],
    "F2": [int(w) for w in _HELV_BOLD.split()],
}

# Characters a model reliably emits that aren't ASCII. WinAnsiEncoding has them,
# so map rather than drop — an em dash becoming "?" looks careless on a resume.
WINANSI = {
    "–": 0x96, "—": 0x97, "‘": 0x91, "’": 0x92,
    "“": 0x93, "”": 0x94, "•": 0x95, "·": 0xB7,
    "…": 0x85, "é": 0xE9, " ": 0x20, "₹": 0x52,  # ₹ -> R
}


def text_width(text, font, size):
    """Width of `text` in points."""
    table = WIDTHS[font]
    total = 0
    for ch in text:
        code = ord(ch)
        if 32 <= code <= 126:
            total += table[code - 32]
        elif ch in WINANSI:
            wa = WINANSI[ch]
            total += table[wa - 32] if 32 <= wa <= 126 else 556
        else:
            total += 556
    return total * size / 1000.0


def wrap(text, font, size, max_width):
    """Greedy word wrap using real font metrics."""
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if line and text_width(trial, font, size) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines or [""]


def _escape(text):
    """Encode a string for a PDF literal, mapping smart punctuation to WinAnsi."""
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if ch in WINANSI:
            code = WINANSI[ch]
        elif code > 255:
            code = ord("?")
        if code in (0x28, 0x29, 0x5C):  # ( ) \
            out.append(0x5C)
        out.append(code)
    return bytes(out)


class Document:
    """A single-column text resume, paginated automatically."""

    def __init__(self, width=595, height=842, margin=52, leading=13.2):
        self.W, self.H, self.M = width, height, margin
        self.leading = leading
        self.pages, self.ops = [], []
        self.y = height - margin
        self.col = width - 2 * margin

    # --- layout primitives ---------------------------------------------------

    def _room(self, needed):
        """Break the page when the next block would cross the bottom margin."""
        if self.y - needed < self.M:
            self._flush_page()

    def _flush_page(self):
        if self.ops:
            self.pages.append("\n".join(self.ops))
        self.ops = []
        self.y = self.H - self.M

    def _line(self, text, font="F1", size=9.6, x=None, gap=None):
        gap = self.leading if gap is None else gap
        self._room(gap)
        x = self.M if x is None else x
        self.ops.append(
            f"BT /{font} {size:.1f} Tf 1 0 0 1 {x:.1f} {self.y:.1f} Tm "
            f"({_escape(text).decode('latin-1')}) Tj ET"
        )
        self.y -= gap

    def space(self, amount=5):
        self.y -= amount

    def rule(self, gap=7):
        """Hairline under a section heading. Vector, so ATS ignores it."""
        self._room(gap)
        self.ops.append(
            f"0.75 w 0.62 G {self.M} {self.y + 3.4:.1f} m "
            f"{self.W - self.M} {self.y + 3.4:.1f} l S"
        )
        self.y -= gap

    # --- resume blocks -------------------------------------------------------

    def name(self, text):
        self._line(text, font="F2", size=19, gap=21)

    def tagline(self, text):
        for ln in wrap(text, "F1", 9.6, self.col):
            self._line(ln, size=9.6, gap=12.4)

    def contact(self, parts):
        self._line("  |  ".join(p for p in parts if p), size=9, gap=15)

    def section(self, title):
        # Keep a heading with at least one following line.
        self._room(self.leading * 3)
        self._line(title.upper(), font="F2", size=9.9, gap=4.6)
        self.rule()

    def job(self, company, title, dates, location=""):
        left = company
        if title:
            left += f" — {title}"
        if location:
            left += f", {location}"

        # Dates sit flush right on the first baseline, so the left text has to be
        # wrapped inside the space that actually remains. Without this, a long
        # entry (a full university name plus degree) runs straight into the date.
        dates_w = text_width(dates, "F1", 8.9) if dates else 0
        first_col = self.col - dates_w - 14 if dates else self.col
        lines = wrap(left, "F2", 9.8, max(first_col, 120))
        head, rest = lines[0], lines[1:]
        if rest:
            rest = wrap(" ".join(rest), "F2", 9.8, self.col)

        self._room(self.leading * (1.4 + len(rest)))
        y_at = self.y
        self._line(head, font="F2", size=9.8, gap=0)
        self.y = y_at
        if dates:
            self._line(dates, font="F1", size=8.9,
                       x=self.W - self.M - dates_w,
                       gap=13.4 if not rest else self.leading)
        else:
            self.y -= 13.4 if not rest else self.leading
        for i, ln in enumerate(rest):
            self._line(ln, font="F2", size=9.8,
                       gap=13.4 if i == len(rest) - 1 else self.leading)

    def bullet(self, text, size=9.5):
        indent = 11.5
        lines = wrap(text, "F1", size, self.col - indent)
        self._room(self.leading * len(lines))
        y_at = self.y
        self._line("•", size=size, gap=0)
        self.y = y_at
        for i, ln in enumerate(lines):
            self._line(ln, size=size, x=self.M + indent,
                       gap=self.leading if i < len(lines) - 1 else self.leading)

    def labelled(self, label, text, size=9.5):
        """'Strong: Figma, Illustrator...' — label bold, body wrapped under it."""
        head = f"{label}: "
        offset = text_width(head, "F2", size)
        first = wrap(text, "F1", size, self.col - offset)
        rest = []
        if len(first) > 1:
            joined = " ".join(first[1:])
            rest = wrap(joined, "F1", size, self.col)
            first = first[:1]
        self._room(self.leading * (1 + len(rest)))
        y_at = self.y
        self._line(head, font="F2", size=size, gap=0)
        self.y = y_at
        self._line(first[0], size=size, x=self.M + offset, gap=self.leading)
        for ln in rest:
            self._line(ln, size=size, gap=self.leading)

    def para(self, text, size=9.5):
        for ln in wrap(text, "F1", size, self.col):
            self._line(ln, size=size)

    # --- output --------------------------------------------------------------

    def save(self, path):
        self._flush_page()
        if not self.pages:
            raise ValueError("nothing to write — the document is empty")

        objects, page_ids = [], []
        n_pages = len(self.pages)
        # 1 catalog, 2 pages tree, 3-4 fonts, then page/content pairs.
        first_page_obj = 5

        objects.append("<< /Type /Catalog /Pages 2 0 R >>")
        for i in range(n_pages):
            page_ids.append(first_page_obj + i * 2)
        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        objects.append(f"<< /Type /Pages /Count {n_pages} /Kids [{kids}] >>")
        for base in ("Helvetica", "Helvetica-Bold"):
            objects.append(
                f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} "
                f"/Encoding /WinAnsiEncoding >>"
            )

        streams = {}
        for i, content in enumerate(self.pages):
            page_obj = first_page_obj + i * 2
            content_obj = page_obj + 1
            objects.append(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.W} {self.H}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                f"/Contents {content_obj} 0 R >>"
            )
            packed = zlib.compress(content.encode("latin-1"))
            objects.append(
                f"<< /Length {len(packed)} /Filter /FlateDecode >>"
            )
            streams[len(objects)] = packed  # 1-indexed object number

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for num, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{num} 0 obj\n{body}\n".encode("latin-1")
            if num in streams:
                out += b"stream\n" + streams[num] + b"\nendstream\n"
            out += b"endobj\n"

        xref_at = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n"
        ).encode()

        with open(path, "wb") as fh:
            fh.write(bytes(out))
        return n_pages
