"""Persistent offline wire regressions using synthetic inputs only.

Independent PyCryptodome AES literals and raw SDK instruction component literals
cover inner encryption/framing. Header component vectors use synthetic converted
inputs and length-aware byte fields that preserve NULs. Device component vectors
use hooks supplied with post-Windows-conversion bytes and synthetic numeric boxes.
These tests do not execute metadata collection, hardware/profile builders, or
Windows ACP conversion. The all-field profile is synthetic. Component fixtures
do not establish server interoperability.
"""
from __future__ import annotations

import builtins
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import time
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core/qimei_wire.py"
SDK_INNER_FIXTURE = Path(__file__).with_name("qimei_inner_sdk_vectors.json")
SDK_DEVICE_FIXTURE = Path(__file__).with_name("qimei_device_sdk_vectors.json")
SDK_HEADER_FIXTURE = Path(__file__).with_name("qimei_header_sdk_vectors.json")
LIMIT = 1 << 20
EMPTY_HEADER_HEX = (
    "08001200180322002a07352e312e342e3232003a0042004a0052005a0062006800"
)


def minimal_fields():
    fields = {n: b"" for n in range(1, 14)}
    fields.update({1: 0, 3: 3, 5: b"5.1.4.2", 13: 0})
    return fields


class IntSubclass(int):
    pass


class BytesSubclass(bytes):
    pass


class DictSubclass(dict):
    pass


class WireTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.is_file(), "offline QIMEI wire backend is not implemented")
        spec = importlib.util.spec_from_file_location("qimei_wire_regression_backend", SOURCE)
        self.wire = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(self.wire)

    def assert_rejected(self, function, *args, **kwargs):
        with self.assertRaises(self.wire.QimeiWireError) as caught:
            function(*args, **kwargs)
        self.assertIsInstance(caught.exception, RuntimeError)
        self.assertNotIn("sensitive", str(caught.exception))

    def device_payload_encoder(self):
        encoder = getattr(self.wire, "encode_device_payload", None)
        self.assertTrue(callable(encoder), "offline QIMEI device payload encoder is not implemented")
        return encoder

    def test_device_payload_omits_missing_fields_and_empty_byte_inputs(self):
        encode = self.device_payload_encoder()
        self.assertEqual(encode({}), b"")
        self.assertEqual(encode({2: b"", 15: b"", 16: b"", 57: b""}), b"")

    def test_device_payload_preserves_post_conversion_c_string_bytes(self):
        encode = self.device_payload_encoder()
        vectors = [
            ({2: b"\x00x"}, "1200"),
            ({2: b"a\x00b"}, "120161"),
            ({2: b"\xff\x80"}, "1202ff80"),
            ({2: b"", 3: b"\x00x", 57: b"\xff\x80\x00tail"}, "1a00ca0302ff80"),
        ]
        for fields, expected_hex in vectors:
            with self.subTest(fields=fields):
                self.assertEqual(encode(fields), bytes.fromhex(expected_hex))

    def test_device_payload_accepts_every_verified_byte_field(self):
        encode = self.device_payload_encoder()
        fields = {n: b"x" for n in range(1, 58) if n not in (31, 49, 52, 53)}
        expected = bytes.fromhex(
            "0a01781201781a01782201782a01783201783a01784201784a01785201785a0178"
            "6201786a01787201787a0178820101788a010178920101789a010178a2010178"
            "aa010178b2010178ba010178c2010178ca010178d2010178da010178e2010178"
            "ea010178f2010178820201788a020178920201789a020178a2020178aa020178"
            "b2020178ba020178c2020178ca020178d2020178da020178e2020178ea020178"
            "f2020178fa02017882030178920301789a030178b2030178ba030178c2030178ca030178"
        )
        self.assertEqual(encode(fields), expected)

    def test_device_payload_encodes_proven_pid_and_extra_map_text_fields(self):
        # Persistent native literals: field1 producer is GetCurrentProcessId;
        # field50 producer serializes the builder's optional extra map.
        encode = self.device_payload_encoder()
        vectors = [
            ({1: b"1234"}, "0a0431323334"),
            ({1: b"\x00pid"}, "0a00"),
            ({1: b""}, ""),
            ({50: b'{"synthetic":"x"}'}, "9203117b2273796e746865746963223a2278227d"),
            ({50: b"\x00extras"}, "920300"),
            ({50: b""}, ""),
            ({50: b"x", 1: b"1234"}, "0a043132333492030178"),
        ]
        for fields, expected_hex in vectors:
            with self.subTest(fields=fields):
                self.assertEqual(encode(fields), bytes.fromhex(expected_hex))

    def test_device_payload_sorts_wire_numbers_and_encodes_multibyte_tags(self):
        encode = self.device_payload_encoder()
        fields = {57: b"z", 53: 0, 49: 300, 32: b"x", 16: b"a", 15: b"b", 2: b"c"}
        self.assertEqual(encode(fields), bytes.fromhex(
            "1201637a016282010161820201788803ac02a80300ca03017a"))

    def test_device_payload_numeric_zero_and_uint32_bounds_match_literals(self):
        encode = self.device_payload_encoder()
        vectors = [
            ({49: 0, 53: 0xffffffff}, "880300a803ffffffff0f"),
            ({53: 0, 49: 0xffffffff}, "8803ffffffff0fa80300"),
            ({49: 0x80000000, 53: 128}, "88038080808008a8038001"),
        ]
        for fields, expected_hex in vectors:
            with self.subTest(fields=fields):
                self.assertEqual(encode(fields), bytes.fromhex(expected_hex))

    def test_device_payload_requires_an_exact_dict(self):
        encode = self.device_payload_encoder()
        for fields in (None, [], (), ((2, b"x"),), DictSubclass({2: b"x"})):
            with self.subTest(container=type(fields).__name__):
                self.assert_rejected(encode, fields)

    def test_device_payload_rejects_holes_unknown_and_non_exact_integer_keys(self):
        encode = self.device_payload_encoder()
        for key in (31, 52, 0, -1, 58, True, 2.0, "2", IntSubclass(2)):
            with self.subTest(key=repr(key), kind=type(key).__name__):
                self.assert_rejected(encode, {key: b"sensitive"})

    def test_device_payload_byte_fields_require_exact_bytes(self):
        encode = self.device_payload_encoder()
        for number in (1, 2, 15, 16, 30, 32, 48, 50, 51, 54, 57):
            for value in ("sensitive", bytearray(b"sensitive"), memoryview(b"sensitive"),
                          BytesSubclass(b"sensitive"), None, 0, True):
                with self.subTest(field=number, kind=type(value).__name__):
                    self.assert_rejected(encode, {number: value})

    def test_device_payload_numeric_fields_require_exact_uint32(self):
        encode = self.device_payload_encoder()
        for number in (49, 53):
            for value in (-1, 1 << 32, True, False, 1.0, "sensitive", IntSubclass(1), None, b""):
                with self.subTest(field=number, kind=type(value).__name__):
                    self.assert_rejected(encode, {number: value})

    def test_device_payload_budget_includes_tag_and_length_varints(self):
        encode = self.device_payload_encoder()
        for number, overhead, prefix in ((2, 4, b"\x12\xfc\xff\x3f"),
                                         (57, 5, b"\xca\x03\xfb\xff\x3f")):
            with self.subTest(field=number):
                value = b"x" * (LIMIT - overhead)
                self.assertEqual(encode({number: value}), prefix + value)
                self.assert_rejected(encode, {number: value + b"x"})

    def test_device_payload_final_budget_uses_truncated_prefix_lengths(self):
        encode = self.device_payload_encoder()
        value = b"x\x00" + b"y" * (LIMIT - 2)
        self.assertEqual(encode({2: value, 57: value}), bytes.fromhex("120178ca030178"))

    def test_device_payload_rejects_raw_oversize_even_with_leading_nul(self):
        encode = self.device_payload_encoder()
        self.assert_rejected(encode, {2: b"\x00" + b"x" * LIMIT})

    def test_device_payload_rejects_aggregate_oversize_before_serializing(self):
        encode = self.device_payload_encoder()
        fields = {2: b"x" * (LIMIT // 2), 57: b"y" * (LIMIT // 2)}
        with patch.object(self.wire, "_append_varint", side_effect=AssertionError("writer reached")):
            self.assert_rejected(encode, fields)

    def test_device_payload_matches_raw_sdk_post_conversion_component_literals(self):
        encode = self.device_payload_encoder()
        vectors = json.loads(SDK_DEVICE_FIXTURE.read_text(encoding="utf-8"))["vectors"]
        self.assertEqual(len(vectors), 60)
        for vector in vectors:
            with self.subTest(native_case=vector["name"]):
                fields = {}
                for number, value in vector["fields"].items():
                    kind = vector["types"][number]
                    if kind == "post_conversion_bytes":
                        fields[int(number)] = bytes.fromhex(value)
                    else:
                        # The int32 kind4-box hook records its low uint32 value.
                        self.assertEqual(kind, "int32")
                        fields[int(number)] = value
                self.assertEqual(encode(fields), bytes.fromhex(vector["native_body_hex"]))

    def test_header_preserves_every_zero_and_empty_field_as_literal_bytes(self):
        self.assertEqual(self.wire.encode_header(minimal_fields()),
                         bytes.fromhex(EMPTY_HEADER_HEX))

    def test_header_all_fields_and_negative_int32_match_literal(self):
        fields = {1: 300, 2: b"\x00\xff", 3: 3, 4: b"app", 5: b"5.1.4.2",
                  6: b"x", 7: b"\x00", 8: b"", 9: b"q", 10: b"t", 11: b"u",
                  12: b"\xff", 13: -1}
        expected = bytes.fromhex(
            "08ac02120200ff180322036170702a07352e312e342e323201783a0100"
            "42004a01715201745a01756201ff68ffffffffffffffffff01"
        )
        self.assertEqual(self.wire.encode_header(fields), expected)

    def test_uint32_max_and_int32_min_have_literal_varint_encodings(self):
        fields = minimal_fields()
        fields[1], fields[13] = 0xffffffff, -(1 << 31)
        expected = bytes.fromhex(
            "08ffffffff0f1200180322002a07352e312e342e3232003a0042004a00"
            "52005a0062006880808080f8ffffffff01"
        )
        self.assertEqual(self.wire.encode_header(fields), expected)

    def test_header_order_does_not_follow_dict_insertion_order(self):
        fields = dict(reversed(list(minimal_fields().items())))
        self.assertEqual(self.wire.encode_header(fields), bytes.fromhex(EMPTY_HEADER_HEX))

    def test_header_matches_raw_sdk_synthetic_component_literals(self):
        # Native 4E008/writer outputs use directly populated synthetic inputs;
        # the length-aware header path preserves NULs unlike device strlen.
        vectors = json.loads(SDK_HEADER_FIXTURE.read_text(encoding="utf-8"))["vectors"]
        self.assertEqual(len(vectors), 20)
        for vector in vectors:
            with self.subTest(native_case=vector["name"]):
                fields = {}
                for number, value in vector["fields"].items():
                    kind = vector["types"][number]
                    if kind == "post_conversion_bytes":
                        fields[int(number)] = bytes.fromhex(value)
                    else:
                        self.assertIn(kind, ("uint32", "signed_int32"))
                        fields[int(number)] = value
                self.assertEqual(self.wire.encode_header(fields),
                                 bytes.fromhex(vector["native_header_hex"]))

    def test_header_requires_exact_dict_and_exact_complete_integer_keys(self):
        for value in (None, [], tuple(minimal_fields().items()), DictSubclass(minimal_fields())):
            with self.subTest(container=type(value).__name__):
                self.assert_rejected(self.wire.encode_header, value)
        missing = minimal_fields()
        del missing[12]
        extra = {**minimal_fields(), 14: b"sensitive"}
        bool_key = minimal_fields()
        del bool_key[1]
        bool_key[True] = 0
        subclass_key = minimal_fields()
        del subclass_key[1]
        subclass_key[IntSubclass(1)] = 0
        for fields in (missing, extra, bool_key, subclass_key):
            self.assert_rejected(self.wire.encode_header, fields)

    def test_header_unsigned_fields_reject_overflow_bool_and_coercions(self):
        for value in (-1, 1 << 32, True, False, 1.0, "sensitive", IntSubclass(1)):
            for number in (1, 3):
                with self.subTest(field=number, kind=type(value).__name__):
                    self.assert_rejected(self.wire.encode_header,
                                         {**minimal_fields(), number: value})

    def test_header_signed_field_rejects_overflow_bool_and_coercions(self):
        for value in (-(1 << 31) - 1, 1 << 31, True, 1.0, "sensitive", IntSubclass(0)):
            with self.subTest(kind=type(value).__name__):
                self.assert_rejected(self.wire.encode_header, {**minimal_fields(), 13: value})

    def test_header_byte_fields_are_exact_bytes(self):
        for value in ("sensitive", bytearray(b"sensitive"), memoryview(b"sensitive"),
                      BytesSubclass(b"sensitive"), None, 0):
            for number in (2, 4, 5, 6, 7, 8, 9, 10, 11, 12):
                with self.subTest(field=number, kind=type(value).__name__):
                    self.assert_rejected(self.wire.encode_header,
                                         {**minimal_fields(), number: value})

    def test_header_enforces_observed_platform_and_sdk_values(self):
        self.assert_rejected(self.wire.encode_header, {**minimal_fields(), 3: 2})
        self.assert_rejected(self.wire.encode_header, {**minimal_fields(), 5: b"sensitive"})

    def test_header_encoded_budget_includes_varint_length_overhead(self):
        # The hand-derived minimal header is 33 bytes. A three-byte length adds two.
        fields = {**minimal_fields(), 2: b"x" * (LIMIT - 35)}
        self.assertEqual(len(self.wire.encode_header(fields)), LIMIT)
        fields[2] += b"x"
        self.assert_rejected(self.wire.encode_header, fields)

    def test_header_rejects_single_and_aggregate_oversize_inputs(self):
        for fields in ({**minimal_fields(), 2: b"x" * (LIMIT + 1)},
                       {**minimal_fields(), 2: b"x" * (LIMIT // 2),
                        4: b"y" * (LIMIT // 2)}):
            self.assert_rejected(self.wire.encode_header, fields)

    def test_inner_frame_matches_independent_literal_aes_vectors(self):
        # Generated once with independent PyCryptodome AES over hand-written outer
        # protobuf literals. Tests intentionally do not import Crypto or a builder.
        vectors = [
            (b"", b"", bytes.fromhex("FF802003F67F55BE8B535A97B01B459D"), 0,
             "03000000005789207d1553d06914f6553b4547fe32"),
            (b"A", b"B", bytes(range(16)), 0x12345678,
             "0312345678cf0b0e0ceb54736cb79286e7cb238030"),
            (b"A" * 12, b"", bytes(range(16)), 1,
             "0300000001b4a5bf7bdc48152edd5827b7ddfda97a6dfbfea426d03726c0bd967a19a8a8fb"),
            (b"\x00\xff" * 5, bytes(range(32)),
             bytes.fromhex("FF802003F67F55BE8B535A97B01B459D"), 0xffffffff,
             "03ffffffffb3c8bb5388ad8cb05eb4ea8921d64286b017bc087489170d"
             "b83f39eac6fbb2bf32cf08939da1b85537f7ff76a227092a"),
        ]
        for header, body, key, version, expected_hex in vectors:
            with self.subTest(header_size=len(header), body_size=len(body), version=version):
                self.assertEqual(self.wire.encode_inner_frame(header, body, key=key,
                                                              version=version),
                                 bytes.fromhex(expected_hex))

    def test_inner_frame_matches_raw_sdk_component_literals(self):
        # These expected bytes come from the fixture's recorded raw x64 SDK
        # crypto/glue instruction scope. Runtime tests execute no DLL/emulator.
        vectors = json.loads(SDK_INNER_FIXTURE.read_text(encoding="utf-8"))["vectors"]
        self.assertEqual(len(vectors), 24)
        fallback_key = bytes.fromhex("FF802003F67F55BE8B535A97B01B459D")
        for vector in vectors:
            with self.subTest(native_case=vector["name"]):
                key_hex = vector["key_override_hex"]
                key = fallback_key if key_hex is None else bytes.fromhex(key_hex)
                self.assertEqual(self.wire.encode_inner_frame(
                    bytes.fromhex(vector["header_hex"]),
                    bytes.fromhex(vector["body_hex"]),
                    version=vector["version"], key=key),
                    bytes.fromhex(vector["native_frame_hex"]))

    def test_inner_frame_inputs_are_exact_bytes(self):
        for value in ("sensitive", bytearray(b"sensitive"), memoryview(b"sensitive"),
                      BytesSubclass(b"sensitive"), None):
            for position in (0, 1):
                inputs = [b"", b""]
                inputs[position] = value
                self.assert_rejected(self.wire.encode_inner_frame, *inputs,
                                     version=1, key=bytes(16))

    def test_inner_frame_requires_exact_16_byte_key(self):
        for value in (b"", bytes(15), bytes(17), bytes(32), bytearray(16),
                      BytesSubclass(bytes(16)), "sensitive", None):
            self.assert_rejected(self.wire.encode_inner_frame, b"", b"", version=1, key=value)

    def test_inner_frame_version_is_exact_uint32(self):
        for value in (-1, 1 << 32, True, False, 1.0, "sensitive", IntSubclass(1), None):
            self.assert_rejected(self.wire.encode_inner_frame, b"", b"", version=value,
                                 key=bytes(16))

    def test_inner_frame_accepts_exact_outer_protobuf_budget(self):
        # tag1 + three-byte length + header + tag2 + empty length = header + 6.
        result = self.wire.encode_inner_frame(b"x" * (LIMIT - 6), b"", version=1,
                                              key=bytes(16))
        self.assertEqual(result[:5], b"\x03\x00\x00\x00\x01")
        self.assertEqual(len(result), LIMIT + 16 + 5)  # Full PKCS#7 block.

    def test_inner_frame_rejects_oversize_before_encryption(self):
        # A cipher must never receive data rejected by the serialized-size budget.
        with patch.object(self.wire, "Cipher", side_effect=AssertionError("cipher reached")):
            for header, body in ((b"x" * (LIMIT + 1), b""), (b"x" * LIMIT, b""),
                                 (b"x" * (LIMIT // 2), b"y" * (LIMIT // 2))):
                self.assert_rejected(self.wire.encode_inner_frame, header, body,
                                     version=1, key=bytes(16))

    def test_crypto_errors_are_sanitized(self):
        with patch.object(self.wire, "Cipher", side_effect=ValueError("sensitive key")):
            self.assert_rejected(self.wire.encode_inner_frame, b"", b"", version=1,
                                 key=bytes(16))

    def test_encoders_need_no_io_clock_or_randomness(self):
        # Load the cryptography backend before guarding the encoding-only path.
        self.wire.encode_inner_frame(b"", b"", version=0,
                                     key=self.wire.FALLBACK_INNER_KEY)
        with patch.object(builtins, "open", side_effect=AssertionError("file IO")), \
             patch.object(io, "open", side_effect=AssertionError("file IO")), \
             patch.object(os, "open", side_effect=AssertionError("file IO")), \
             patch.object(os, "urandom", side_effect=AssertionError("randomness")), \
             patch.object(time, "time", side_effect=AssertionError("clock")), \
             patch.object(socket, "socket", side_effect=AssertionError("network")):
            self.assertEqual(self.wire.encode_header(minimal_fields()),
                             bytes.fromhex(EMPTY_HEADER_HEX))
            self.assertEqual(self.device_payload_encoder()({2: b"a\x00b", 49: 0, 57: b"\xff\x80"}),
                             bytes.fromhex("120161880300ca0302ff80"))
            self.assertEqual(self.wire.encode_inner_frame(
                b"", b"", version=0, key=self.wire.FALLBACK_INNER_KEY),
                bytes.fromhex("03000000005789207d1553d06914f6553b4547fe32"))


if __name__ == "__main__":
    unittest.main()
