// Non-template part of the C++ test harness, compiled once at image build
// time into sandbox_harness.o (same flags as submissions) and linked into
// test-mode programs. See sandbox_prelude.hpp.
#include "sandbox_prelude.hpp"

#include <cxxabi.h>
#include <fcntl.h>
#include <unistd.h>

namespace sbx {
namespace {

class Parser {
public:
    explicit Parser(const std::string& s) : s_(s) {}
    Json parse() {
        ws();
        Json v = value(0);
        ws();
        if (p_ != s_.size()) fail("trailing data");
        return v;
    }

private:
    const std::string& s_;
    size_t p_ = 0;

    [[noreturn]] void fail(const char* what) { throw ParseError(std::string("invalid JSON: ") + what); }
    void ws() {
        while (p_ < s_.size() && (s_[p_] == ' ' || s_[p_] == '\n' || s_[p_] == '\r' || s_[p_] == '\t')) ++p_;
    }
    void lit(const char* w) {
        size_t n = std::strlen(w);
        if (s_.compare(p_, n, w) != 0) fail("bad literal");
        p_ += n;
    }
    static void put_utf8(std::string& out, unsigned cp) {
        if (cp < 0x80) {
            out += static_cast<char>(cp);
        } else if (cp < 0x800) {
            out += static_cast<char>(0xC0 | (cp >> 6));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            out += static_cast<char>(0xE0 | (cp >> 12));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (cp >> 18));
            out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        }
    }
    unsigned hex4() {
        if (p_ + 4 > s_.size()) fail("bad unicode escape");
        unsigned v = 0;
        for (int k = 0; k < 4; ++k) {
            char c = s_[p_++];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= static_cast<unsigned>(c - '0');
            else if (c >= 'a' && c <= 'f') v |= static_cast<unsigned>(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= static_cast<unsigned>(c - 'A' + 10);
            else fail("bad unicode escape");
        }
        return v;
    }
    std::string str() {
        std::string out;
        ++p_;
        while (true) {
            if (p_ >= s_.size()) fail("unterminated string");
            char c = s_[p_++];
            if (c == '"') return out;
            if (c != '\\') {
                out += c;
                continue;
            }
            if (p_ >= s_.size()) fail("bad escape");
            char e = s_[p_++];
            switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    unsigned cp = hex4();
                    if (cp >= 0xD800 && cp <= 0xDBFF && p_ + 6 <= s_.size() && s_[p_] == '\\' && s_[p_ + 1] == 'u') {
                        p_ += 2;
                        unsigned lo = hex4();
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    }
                    put_utf8(out, cp);
                    break;
                }
                default: fail("bad escape");
            }
        }
    }
    Json value(int depth) {
        if (depth > 512) fail("nesting too deep");
        if (p_ >= s_.size()) fail("unexpected end");
        Json v;
        char c = s_[p_];
        if (c == '{') {
            v.kind = Json::Object;
            ++p_;
            ws();
            if (p_ < s_.size() && s_[p_] == '}') { ++p_; return v; }
            while (true) {
                ws();
                if (p_ >= s_.size() || s_[p_] != '"') fail("expected key");
                std::string k = str();
                ws();
                if (p_ >= s_.size() || s_[p_] != ':') fail("expected ':'");
                ++p_;
                ws();
                v.fields.emplace_back(std::move(k), value(depth + 1));
                ws();
                if (p_ >= s_.size()) fail("unterminated object");
                char d = s_[p_++];
                if (d == '}') return v;
                if (d != ',') fail("expected ','");
            }
        }
        if (c == '[') {
            v.kind = Json::Array;
            ++p_;
            ws();
            if (p_ < s_.size() && s_[p_] == ']') { ++p_; return v; }
            while (true) {
                ws();
                v.items.push_back(value(depth + 1));
                ws();
                if (p_ >= s_.size()) fail("unterminated array");
                char d = s_[p_++];
                if (d == ']') return v;
                if (d != ',') fail("expected ','");
            }
        }
        if (c == '"') {
            v.kind = Json::String;
            v.text = str();
            return v;
        }
        if (c == 't') { lit("true"); v.kind = Json::Bool; v.b = true; return v; }
        if (c == 'f') { lit("false"); v.kind = Json::Bool; v.b = false; return v; }
        if (c == 'n') { lit("null"); return v; }
        size_t start = p_;
        if (s_[p_] == '-') ++p_;
        while (p_ < s_.size() && (std::isdigit(static_cast<unsigned char>(s_[p_])) || s_[p_] == '.' || s_[p_] == 'e' ||
                                  s_[p_] == 'E' || s_[p_] == '+' || s_[p_] == '-'))
            ++p_;
        if (start == p_) fail("unexpected character");
        v.kind = Json::Number;
        v.text = s_.substr(start, p_ - start);
        return v;
    }
};

std::string demangle(const char* name) {
    int status = 0;
    char* out = abi::__cxa_demangle(name, nullptr, nullptr, &status);
    std::string result = (status == 0 && out) ? out : name;
    std::free(out);
    return result;
}

void emit(const std::string& line) {
    std::string data = line + "\n";
    const char* p = data.data();
    size_t left = data.size();
    while (left > 0) {
        ssize_t n = ::write(3, p, left);
        if (n < 0) {
            if (errno == EINTR) continue;
            ::_exit(3);
        }
        p += n;
        left -= static_cast<size_t>(n);
    }
}

std::string ms_field(double ms) {
    char buf[48];
    std::snprintf(buf, sizeof buf, "%.6f", ms);
    return buf;
}

}  // namespace

Json parse_json(const std::string& text) { return Parser(text).parse(); }

long long to_ll(const Json& j) {
    if (j.kind != Json::Number) throw ParseError("expected a number");
    errno = 0;
    char* end = nullptr;
    long long v = std::strtoll(j.text.c_str(), &end, 10);
    if (*end != '\0') {
        double d = std::strtod(j.text.c_str(), &end);
        if (d != std::floor(d)) throw ParseError("expected an integer, got " + j.text);
        return static_cast<long long>(d);
    }
    if (errno == ERANGE) throw ParseError("integer out of range: " + j.text);
    return v;
}

double to_double(const Json& j) {
    if (j.kind != Json::Number) throw ParseError("expected a number");
    return std::strtod(j.text.c_str(), nullptr);
}

void put_string(std::string& out, const std::string& s) {
    out += '"';
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof buf, "\\u%04x", c);
                    out += buf;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    out += '"';
}

void put_double(std::string& out, double d) {
    if (std::isnan(d)) {
        out += "\"NaN\"";
    } else if (std::isinf(d)) {
        out += d > 0 ? "\"Infinity\"" : "\"-Infinity\"";
    } else {
        char buf[40];
        std::snprintf(buf, sizeof buf, "%.17g", d);
        out += buf;
    }
}

bool Harness::load() {
    std::string input((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
    std::cin.clear();
    int devnull = ::open("/dev/null", O_RDONLY);
    if (devnull >= 0) {
        ::dup2(devnull, 0);
        ::close(devnull);
    }
    try {
        Json root = parse_json(input);
        for (const auto& f : root.fields) {
            if (f.first != "tests") continue;
            for (const auto& t : f.second.items) {
                Test test;
                for (const auto& tf : t.fields) {
                    if (tf.first == "i") test.index = to_ll(tf.second);
                    else if (tf.first == "args") test.args = tf.second.items;
                }
                tests.push_back(std::move(test));
            }
        }
    } catch (const std::exception& e) {
        fatal("harness", std::string("invalid test input: ") + e.what());
        return false;
    }
    return true;
}

void Harness::ready() { emit("{\"t\":\"ready\"}"); }

void Harness::begin(const Test& t) { emit("{\"t\":\"begin\",\"i\":" + std::to_string(t.index) + "}"); }

void Harness::done() {
    std::cout.flush();
    std::fflush(stdout);
    emit("{\"t\":\"done\"}");
}

void Harness::result(const Test& t, const std::string& actual, double ms) {
    emit("{\"t\":\"result\",\"i\":" + std::to_string(t.index) + ",\"ok\":true,\"actual\":" + actual + ",\"ms\":" + ms_field(ms) + "}");
}

void Harness::error(const Test& t, const std::string& type, const std::string& message, double ms) {
    std::string line = "{\"t\":\"result\",\"i\":" + std::to_string(t.index) + ",\"ok\":false,\"error_type\":";
    put_string(line, type);
    line += ",\"error\":";
    put_string(line, message);
    if (ms >= 0) line += ",\"ms\":" + ms_field(ms);
    emit(line + "}");
}

void Harness::exception(const Test& t, std::exception_ptr ep, double ms) {
    try {
        std::rethrow_exception(ep);
    } catch (const ParseError& e) {
        error(t, "harness", std::string("bad arguments: ") + e.what());
    } catch (const std::bad_alloc&) {
        error(t, "memory", "std::bad_alloc (out of memory)", ms);
    } catch (const std::exception& e) {
        error(t, "runtime", demangle(typeid(e).name()) + ": " + e.what(), ms);
    } catch (const char* s) {
        error(t, "runtime", std::string("uncaught exception: ") + (s ? s : "null"), ms);
    } catch (const std::string& s) {
        error(t, "runtime", "uncaught exception: " + s, ms);
    } catch (int v) {
        error(t, "runtime", "uncaught exception: " + std::to_string(v), ms);
    } catch (...) {
        error(t, "runtime", "uncaught exception of unknown type", ms);
    }
}

void Harness::fatal(const std::string& kind, const std::string& message) {
    std::string line = "{\"t\":\"fatal\",\"kind\":";
    put_string(line, kind);
    line += ",\"message\":";
    put_string(line, message);
    emit(line + "}");
}

}  // namespace sbx
