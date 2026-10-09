import java.lang.reflect.Array;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/** Minimal JSON reader/writer used by the sandbox harness (no dependencies). */
final class SandboxJson {
    private static final int MAX_DEPTH = 512;
    private final String text;
    private int pos;

    private SandboxJson(String text) {
        this.text = text;
    }

    /** Objects become LinkedHashMap, arrays ArrayList, integers Long or BigInteger, other numbers Double. */
    static Object parse(String text) {
        SandboxJson parser = new SandboxJson(text);
        parser.skipWhitespace();
        Object value = parser.readValue(0);
        parser.skipWhitespace();
        if (parser.pos != text.length()) {
            throw parser.error("unexpected trailing data");
        }
        return value;
    }

    private IllegalArgumentException error(String message) {
        return new IllegalArgumentException("invalid JSON at offset " + pos + ": " + message);
    }

    private void skipWhitespace() {
        while (pos < text.length()) {
            char c = text.charAt(pos);
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                pos++;
            } else {
                break;
            }
        }
    }

    private Object readValue(int depth) {
        if (depth > MAX_DEPTH) {
            throw error("nesting too deep");
        }
        if (pos >= text.length()) {
            throw error("unexpected end of input");
        }
        char c = text.charAt(pos);
        switch (c) {
            case '{':
                return readObject(depth);
            case '[':
                return readArray(depth);
            case '"':
                return readString();
            case 't':
                expect("true");
                return Boolean.TRUE;
            case 'f':
                expect("false");
                return Boolean.FALSE;
            case 'n':
                expect("null");
                return null;
            default:
                if (c == '-' || (c >= '0' && c <= '9')) {
                    return readNumber();
                }
                throw error("unexpected character '" + c + "'");
        }
    }

    private void expect(String word) {
        if (!text.startsWith(word, pos)) {
            throw error("expected " + word);
        }
        pos += word.length();
    }

    private Map<String, Object> readObject(int depth) {
        Map<String, Object> map = new LinkedHashMap<>();
        pos++;
        skipWhitespace();
        if (pos < text.length() && text.charAt(pos) == '}') {
            pos++;
            return map;
        }
        while (true) {
            skipWhitespace();
            if (pos >= text.length() || text.charAt(pos) != '"') {
                throw error("expected object key");
            }
            String key = readString();
            skipWhitespace();
            if (pos >= text.length() || text.charAt(pos) != ':') {
                throw error("expected ':'");
            }
            pos++;
            skipWhitespace();
            map.put(key, readValue(depth + 1));
            skipWhitespace();
            if (pos >= text.length()) {
                throw error("unterminated object");
            }
            char c = text.charAt(pos++);
            if (c == '}') {
                return map;
            }
            if (c != ',') {
                throw error("expected ',' or '}'");
            }
        }
    }

    private List<Object> readArray(int depth) {
        List<Object> list = new ArrayList<>();
        pos++;
        skipWhitespace();
        if (pos < text.length() && text.charAt(pos) == ']') {
            pos++;
            return list;
        }
        while (true) {
            skipWhitespace();
            list.add(readValue(depth + 1));
            skipWhitespace();
            if (pos >= text.length()) {
                throw error("unterminated array");
            }
            char c = text.charAt(pos++);
            if (c == ']') {
                return list;
            }
            if (c != ',') {
                throw error("expected ',' or ']'");
            }
        }
    }

    private String readString() {
        StringBuilder sb = new StringBuilder();
        pos++;
        while (true) {
            if (pos >= text.length()) {
                throw error("unterminated string");
            }
            char c = text.charAt(pos++);
            if (c == '"') {
                return sb.toString();
            }
            if (c != '\\') {
                sb.append(c);
                continue;
            }
            if (pos >= text.length()) {
                throw error("unterminated escape");
            }
            char e = text.charAt(pos++);
            switch (e) {
                case '"': sb.append('"'); break;
                case '\\': sb.append('\\'); break;
                case '/': sb.append('/'); break;
                case 'b': sb.append('\b'); break;
                case 'f': sb.append('\f'); break;
                case 'n': sb.append('\n'); break;
                case 'r': sb.append('\r'); break;
                case 't': sb.append('\t'); break;
                case 'u':
                    if (pos + 4 > text.length()) {
                        throw error("bad unicode escape");
                    }
                    sb.append((char) Integer.parseInt(text.substring(pos, pos + 4), 16));
                    pos += 4;
                    break;
                default:
                    throw error("bad escape '\\" + e + "'");
            }
        }
    }

    private Object readNumber() {
        int start = pos;
        boolean integral = true;
        if (text.charAt(pos) == '-') {
            pos++;
        }
        while (pos < text.length()) {
            char c = text.charAt(pos);
            if (c >= '0' && c <= '9') {
                pos++;
            } else if (c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') {
                integral = false;
                pos++;
            } else {
                break;
            }
        }
        String token = text.substring(start, pos);
        try {
            if (integral) {
                BigInteger big = new BigInteger(token);
                return big.bitLength() < 64 ? (Object) big.longValue() : big;
            }
            return Double.parseDouble(token);
        } catch (NumberFormatException ex) {
            throw error("bad number '" + token + "'");
        }
    }

    // ------------------------------------------------------------------ writer

    static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        writeValue(sb, value, 0);
        return sb.toString();
    }

    private static void writeValue(StringBuilder sb, Object value, int depth) {
        if (depth > MAX_DEPTH) {
            throw new IllegalArgumentException("return value is nested too deeply");
        }
        if (value == null) {
            sb.append("null");
        } else if (value instanceof Boolean) {
            sb.append(value.toString());
        } else if (value instanceof Double || value instanceof Float) {
            double d = ((Number) value).doubleValue();
            if (Double.isNaN(d)) {
                sb.append("\"NaN\"");
            } else if (Double.isInfinite(d)) {
                sb.append(d > 0 ? "\"Infinity\"" : "\"-Infinity\"");
            } else {
                sb.append(value instanceof Float ? Float.toString((Float) value) : Double.toString(d));
            }
        } else if (value instanceof BigDecimal) {
            sb.append(((BigDecimal) value).toString());
        } else if (value instanceof Number) {
            sb.append(value.toString());
        } else if (value instanceof Character || value instanceof CharSequence || value instanceof Enum) {
            writeString(sb, value instanceof Enum ? ((Enum<?>) value).name() : value.toString());
        } else if (value.getClass().isArray()) {
            sb.append('[');
            int n = Array.getLength(value);
            for (int i = 0; i < n; i++) {
                if (i > 0) {
                    sb.append(',');
                }
                writeValue(sb, Array.get(value, i), depth + 1);
            }
            sb.append(']');
        } else if (value instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeString(sb, String.valueOf(entry.getKey()));
                sb.append(':');
                writeValue(sb, entry.getValue(), depth + 1);
            }
            sb.append('}');
        } else if (value instanceof Iterable) {
            sb.append('[');
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeValue(sb, item, depth + 1);
            }
            sb.append(']');
        } else if (value instanceof Optional) {
            writeValue(sb, ((Optional<?>) value).orElse(null), depth + 1);
        } else {
            throw new IllegalArgumentException("cannot report a value of type " + value.getClass().getName());
        }
    }

    static void writeString(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
                default:
                    if (c < 0x20 || (c >= 0xd800 && c <= 0xdfff && !validSurrogate(s, i))) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
    }

    private static boolean validSurrogate(String s, int i) {
        char c = s.charAt(i);
        if (Character.isHighSurrogate(c)) {
            return i + 1 < s.length() && Character.isLowSurrogate(s.charAt(i + 1));
        }
        return i > 0 && Character.isHighSurrogate(s.charAt(i - 1));
    }
}
