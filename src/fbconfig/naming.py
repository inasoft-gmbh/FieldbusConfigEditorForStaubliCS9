"""Auto-numbering of signal names, e.g. prefix 'in_byte_' -> in_byte_0, in_byte_1.

Start value and digit count are configurable and remembered (see settings.py):
  digits=1 -> 0,1,2 ; digits=2 -> 00,01,02 ; digits=3 -> 000,001,002 ...
"""
from dataclasses import dataclass


@dataclass
class NamingScheme:
    prefix: str
    start: int = 0
    digits: int = 1   # zero-padding width (1 = no padding)

    def name(self, ordinal: int) -> str:
        n = self.start + ordinal
        return f"{self.prefix}{n:0{self.digits}d}"

    def names(self, count: int):
        return [self.name(i) for i in range(count)]
