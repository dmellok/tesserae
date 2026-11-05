# Visionect bootloader firmware update protocol

What a Visionect frame's bootloader says when it is flashed over the network,
as measured from one packet capture. `app/visionect_bootloader.py` implements it
without any I/O so it can be moved into `pyvisionect` as it is.

## What was measured

Two captures of Visionect's own Software Suite 7.6.5 flashing a Place & Play 32"
(hardware 1.1.0, hardware name id 8, display id 3255107884), bootloader 6.3.3830:
from firmware 7.4.4407 (`2cb91ed6`) to 7.3.4358 (`a529a106`), and back to
7.4.4407. One frame, two builds. The tables below quote the first; the second has
the same shape, and replaying it through the module reproduces all 233336 bytes
the Suite sent. A decoder over the capture reproduces every
message below byte for byte, including the 232064-byte file the server relayed,
which is identical to the body the Suite downloaded from the repository.

All integers are little-endian. Every bootloader message starts with the word
`0xF0000002` (bytes `02 00 00 f0`); the second word says what it is.

## The conversation

1. The frame is told to reboot with an ordinary command packet (`CommandType 1`).
   About 12 s later its bootloader opens a **new** TCP connection to the same
   server port (11113). It did not on the boot right after the flash, which went
   straight to the normal protocol, so it is not every boot. Frames on Tesserae
   have rebooted normally without an answer, but a hello going unanswered has not
   been captured.
2. **Hello**, frame to server, 88 bytes (22 words):

   | word | value in the capture | meaning |
   |---|---|---|
   | 0 | `0xf0000002` | magic |
   | 1 | 0 | kind: hello |
   | 2 | `0x2cb91ed6` | checksum of the firmware running now (equals the repository's checksum for 7.4.4407) |
   | 3 | `0xffffffff` | unknown |
   | 4 | `0x000a0000` | unknown |
   | 5-7 | `36002000 12473830 39333036` | the 12-byte hardware unique id |
   | 8 | 0 | last 4 bytes of the 16-byte device id |
   | 9-11 | 7, 4, 4407 | firmware major, minor, revision |
   | 12-14 | 6, 3, 3830 | bootloader major, minor, revision |
   | 15 | 8 | hardware name id |
   | 16-18 | 1, 1, 0 | hardware major, minor, BOM |
   | 19 | 0 | unknown |
   | 20 | `0x1e` | unknown |
   | 21 | `0xc205012c` | display id (the `DisplayType` of the normal status packet) |

   The 16-byte device id that the normal protocol uses is bytes 20..35 (words 5
   to 8): `36002000-1247-3830-3933-303600000000` for this frame. Bytes 16..19 are
   not part of it. The field meanings were matched against the repository's
   metadata and the normal status packet (firmware, hardware, display id and
   checksum all agree); they were not confirmed against a log line.
3. **Offer**, server to frame, 16 bytes: magic, `1`, `00 00 ff ff`, the target
   firmware checksum (`06 a1 29 a5` for `a529a106`). No ack for it was seen.
4. **Firmware file**, relayed unchanged, one **record** at a time. A record is
   a 24-byte header and a payload:

   | bytes | value | meaning |
   |---|---|---|
   | 0-3 | `02 00 00 f0` | magic |
   | 4-7 | 2 | kind: record |
   | 8-9 | 1..184 | block index |
   | 10-11 | 9, once in most builds 8; 13 on the last | flags (meaning unknown) |
   | 12-13 | 1616 on the first | payload length, so the record is `24 + length` bytes |
   | 14-15 | 2020; 1452 on the last | unknown |
   | 16-19 | 0..15 | unknown |
   | 20-23 | | looks like ciphertext or a checksum |

   The payload looks encrypted. The file is 184 records ending exactly at its
   end (232064 bytes). Records are found by following the length field from byte
   0, never by searching for the magic, which could occur in ciphertext. Record
   sizes were 56 to 2040 bytes.
5. **Stop and wait.** After each record the frame sends a 16-byte ack: magic,
   `1`, the block index (uint16), `ff ff`, `00 00 00 00`. The server sends the
   next record only after the ack. Acks came 27 ms to 1.16 s after the record (median 50 ms); three took
   about a second (possibly flash erase).
   Offer to first record took 1.3 s, which was the Suite downloading the file.
   Flag 8 is on one block of some builds (block 130 of 7.4.4407) and none of
   others. The frame acked it like any other, 44 ms later, and the slowest ack in
   that flash was 1.7 s.
   The whole flash took about 13 s.
6. After the last ack the Suite closed the connection with a TCP reset 4.6 ms
   later. The frame then booted the new firmware and connected on the normal
   protocol about 10 s later with connect reason "reboot".

The repository (`http://firmware.visionect.com:8089`): `GET /api/v2/list` (the
v1 list lacks this display), JSON with ETag support, entries with
`Meta.AppName`, `type`, `Checksum` (hex, leading zeros dropped), `FwMajor`,
`FwMinor`, `FwRevision`, `HwMajor`, `HwMinor`, `HwBOM`, `HwNameID`, `DisplayID`;
`GET /api/v1/get/<checksum>` returns the file.

## What the module checks

Wrong magic or kind, a hello or ack of the wrong size, an ack for any block but
the one just sent (or a repeat), data the frame had no reason to send, a hello
that does not arrive complete in time, a missing ack, a disconnect, and a file
that does not walk (blocks 1..N with no gap, flags 8 or 9 and 13 on the last,
ends exactly at the end of the file) are all errors. A bootloader other than 6.3
is refused before anything is sent. Nothing is retried.

## What is not known

- What words 3, 4, 19, 20 of the hello, header bytes 10-11 and 14-23 of a record
  mean. The module insists on the flag values 8, 9 and 13 because those are all
  that published builds use; another build may use others and would be refused.
- Whether the firmware is signed or checksummed beyond what the frame checks
  itself, and how the repository's checksum is computed (it is not CRC32 or
  Adler-32 of the file, nor of the payloads). The file is therefore checked only
  for structure, not for integrity.
- What the frame does if a flash is interrupted. It has never been observed. The
  documented way out is the USB Configurator.
- Whether the 1.3 s between offer and first record, or the reset (rather than a
  normal close) after the last ack, matter. Both mirror the capture. The file is
  fetched over plain HTTP with no signature to check, the same as the Suite does;
  its SHA-256 is logged when it is offered.
- Whether the frame needs the hello to be answered within some time (the Suite
  answered in 1.5 ms), whether it acks the offer, and any behaviour on a server
  that sends something other than the Suite did.
- Other hardware, other firmware versions, other bootloader versions, downgrades
  from another family.
