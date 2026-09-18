# Third-party code in this folder

Two files here are not original to this project -- they come from
[QCoDeS](https://github.com/microsoft/Qcodes) and are redistributed under
its MIT license, reproduced in full below.

| File | Origin | Changed? |
|---|---|---|
| `N52xx.py` | `qcodes/instrument_drivers/Keysight/N52xx.py` | yes -- local modifications |
| `KeysightVNA_driver.py` | `qcodes/instrument_drivers/Keysight/Keysight_P5004B.py` | no (renamed only) |

They are vendored rather than imported from the installed `qcodes`
package so this repo runs standalone against a known version of the VNA
driver. Checked against QCoDeS 0.58.0.

The switch driver (`MM4250_QCodes_driver.py` and its commented twin) is
original to this project and covered by the repository's own LICENSE.

---

## QCoDeS license

MIT License

Copyright (c) 2015-2023 by Microsoft Corporation and Københavns
Universitet.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
