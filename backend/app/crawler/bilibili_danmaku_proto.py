from __future__ import annotations


class BilibiliDanmakuProtoError(ValueError):
    pass


def parse_danmaku_segment(payload: bytes) -> list[dict[str, object]]:
    elements: list[dict[str, object]] = []
    offset = 0
    while offset < len(payload):
        tag, offset = _read_varint(payload, offset)
        field_number = tag >> 3
        wire_type = tag & 0x7
        if field_number == 1 and wire_type == 2:
            element_payload, offset = _read_length_delimited(payload, offset)
            elements.append(_parse_danmaku_elem(element_payload))
            continue
        offset = _skip_field(payload, offset, wire_type)
    return elements


def _parse_danmaku_elem(payload: bytes) -> dict[str, object]:
    parsed: dict[str, object] = {}
    offset = 0
    while offset < len(payload):
        tag, offset = _read_varint(payload, offset)
        field_number = tag >> 3
        wire_type = tag & 0x7

        if wire_type == 0:
            value, offset = _read_varint(payload, offset)
        elif wire_type == 2:
            value, offset = _read_length_delimited(payload, offset)
        else:
            offset = _skip_field(payload, offset, wire_type)
            continue

        if field_number == 1 and isinstance(value, int):
            parsed["id"] = value
        elif field_number == 2 and isinstance(value, int):
            parsed["progress"] = value
        elif field_number == 3 and isinstance(value, int):
            parsed["mode"] = value
        elif field_number == 4 and isinstance(value, int):
            parsed["fontsize"] = value
        elif field_number == 5 and isinstance(value, int):
            parsed["color"] = value
        elif field_number == 6 and isinstance(value, bytes):
            parsed["mid_hash"] = _decode_utf8(value)
        elif field_number == 7 and isinstance(value, bytes):
            parsed["content"] = _decode_utf8(value)
        elif field_number == 8 and isinstance(value, int):
            parsed["ctime"] = value
        elif field_number == 9 and isinstance(value, int):
            parsed["weight"] = value
        elif field_number == 10 and isinstance(value, bytes):
            parsed["action"] = _decode_utf8(value)
        elif field_number == 11 and isinstance(value, int):
            parsed["pool"] = value
        elif field_number == 12 and isinstance(value, bytes):
            parsed["id_str"] = _decode_utf8(value)
        elif field_number == 13 and isinstance(value, int):
            parsed["attr"] = value
        elif field_number == 22 and isinstance(value, bytes):
            parsed["animation"] = _decode_utf8(value)

    return parsed


def _decode_utf8(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BilibiliDanmakuProtoError("Danmaku protobuf contained invalid UTF-8") from exc


def _read_varint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(payload):
            raise BilibiliDanmakuProtoError("Unexpected end of protobuf payload")
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
        if shift >= 64:
            raise BilibiliDanmakuProtoError("Varint in protobuf payload was too large")


def _read_length_delimited(payload: bytes, offset: int) -> tuple[bytes, int]:
    length, offset = _read_varint(payload, offset)
    end = offset + length
    if end > len(payload):
        raise BilibiliDanmakuProtoError("Length-delimited protobuf field exceeded payload")
    return payload[offset:end], end


def _skip_field(payload: bytes, offset: int, wire_type: int) -> int:
    if wire_type == 0:
        _, offset = _read_varint(payload, offset)
        return offset
    if wire_type == 1:
        end = offset + 8
        if end > len(payload):
            raise BilibiliDanmakuProtoError("Fixed64 protobuf field exceeded payload")
        return end
    if wire_type == 2:
        _, offset = _read_length_delimited(payload, offset)
        return offset
    if wire_type == 5:
        end = offset + 4
        if end > len(payload):
            raise BilibiliDanmakuProtoError("Fixed32 protobuf field exceeded payload")
        return end
    raise BilibiliDanmakuProtoError(f"Unsupported protobuf wire type {wire_type}")
