// Sandbox C++ prelude, precompiled at image build time (g++ -std=c++17 -O2).
//
// Force-included before every submission (-include sandbox_prelude.hpp), like
// LeetCode's environment: all standard headers plus `using namespace std;`.
// The sbx namespace declares the JSON reader/writer and the test harness used
// by the service-generated glue code. Everything that is not a template lives
// in sandbox_harness.cpp, compiled once into sandbox_harness.o, so a test-mode
// compile only generates code for the candidate and a few template
// instantiations. Expected values never reach this process.
#pragma once
#include <bits/stdc++.h>

using namespace std;

namespace sbx {

struct Json {
    enum Kind { Null, Bool, Number, String, Array, Object } kind = Null;
    bool b = false;
    std::string text;  // number token or string value
    std::vector<Json> items;
    std::vector<std::pair<std::string, Json>> fields;
};

struct ParseError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

Json parse_json(const std::string& text);
long long to_ll(const Json& j);
double to_double(const Json& j);
void put_string(std::string& out, const std::string& s);
void put_double(std::string& out, double d);

// ------------------------------------------------------------------ decoding

template <class T> struct is_vector : std::false_type {};
template <class T, class A> struct is_vector<std::vector<T, A>> : std::true_type {};

template <class T> T from_json(const Json& j) {
    if constexpr (std::is_same_v<T, bool>) {
        if (j.kind != Json::Bool) throw ParseError("expected a boolean");
        return j.b;
    } else if constexpr (std::is_integral_v<T>) {
        long long v = to_ll(j);
        if (v < static_cast<long long>(std::numeric_limits<T>::min()) || v > static_cast<long long>(std::numeric_limits<T>::max()))
            throw ParseError("integer out of range: " + j.text);
        return static_cast<T>(v);
    } else if constexpr (std::is_floating_point_v<T>) {
        return static_cast<T>(to_double(j));
    } else if constexpr (std::is_same_v<T, std::string>) {
        if (j.kind != Json::String) throw ParseError("expected a string");
        return j.text;
    } else if constexpr (is_vector<T>::value) {
        if (j.kind != Json::Array) throw ParseError("expected an array");
        T out;
        out.reserve(j.items.size());
        for (const auto& item : j.items) out.push_back(from_json<typename T::value_type>(item));
        return out;
    } else {
        static_assert(sizeof(T) == 0, "unsupported parameter type");
    }
}

// ------------------------------------------------------------------ encoding

template <class T, class = void> struct is_iterable : std::false_type {};
template <class T>
struct is_iterable<T, std::void_t<decltype(std::begin(std::declval<const T&>())), decltype(std::end(std::declval<const T&>()))>>
    : std::true_type {};
template <class T> struct is_pair : std::false_type {};
template <class A, class B> struct is_pair<std::pair<A, B>> : std::true_type {};
template <class T> struct is_optional : std::false_type {};
template <class T> struct is_optional<std::optional<T>> : std::true_type {};
template <class T, class = void> struct is_map_like : std::false_type {};
template <class T> struct is_map_like<T, std::void_t<typename T::key_type, typename T::mapped_type>> : std::true_type {};

template <class T> void put(std::string& out, const T& v) {
    using U = std::decay_t<T>;
    if constexpr (std::is_same_v<U, bool>) {
        out += v ? "true" : "false";
    } else if constexpr (std::is_same_v<U, char>) {
        put_string(out, std::string(1, v));
    } else if constexpr (std::is_integral_v<U>) {
        out += std::to_string(v);
    } else if constexpr (std::is_floating_point_v<U>) {
        put_double(out, static_cast<double>(v));
    } else if constexpr (std::is_same_v<U, std::string> || std::is_same_v<U, std::string_view>) {
        put_string(out, std::string(v));
    } else if constexpr (std::is_same_v<U, const char*> || std::is_same_v<U, char*>) {
        if (v == nullptr) out += "null"; else put_string(out, std::string(v));
    } else if constexpr (std::is_same_v<U, std::nullptr_t>) {
        out += "null";
    } else if constexpr (is_pair<U>::value) {
        out += '[';
        put(out, v.first);
        out += ',';
        put(out, v.second);
        out += ']';
    } else if constexpr (is_optional<U>::value) {
        if (v) put(out, *v); else out += "null";
    } else if constexpr (is_map_like<U>::value) {
        out += '{';
        bool first = true;
        for (const auto& kv : v) {
            if (!first) out += ',';
            first = false;
            std::string key;
            put(key, kv.first);
            if (key.empty() || key[0] != '"') { std::string quoted; put_string(quoted, key); key = quoted; }
            out += key;
            out += ':';
            put(out, kv.second);
        }
        out += '}';
    } else if constexpr (std::is_same_v<U, std::vector<bool>>) {
        out += '[';
        for (size_t i = 0; i < v.size(); ++i) {
            if (i) out += ',';
            out += v[i] ? "true" : "false";
        }
        out += ']';
    } else if constexpr (is_iterable<U>::value) {
        out += '[';
        bool first = true;
        for (const auto& item : v) {
            if (!first) out += ',';
            first = false;
            put(out, item);
        }
        out += ']';
    } else {
        static_assert(sizeof(U) == 0, "unsupported return type");
    }
}

template <class T> std::string to_json(const T& v) {
    std::string out;
    put(out, v);
    return out;
}

// ------------------------------------------------------------------ harness

struct Test {
    long long index = 0;
    std::vector<Json> args;
};

class Harness {
public:
    std::vector<Test> tests;

    bool load();
    void ready();
    void begin(const Test& t);
    void done();
    void result(const Test& t, const std::string& actual, double ms);
    void error(const Test& t, const std::string& type, const std::string& message, double ms = -1);
    void exception(const Test& t, std::exception_ptr ep, double ms);
    void fatal(const std::string& kind, const std::string& message);

    static double elapsed_ms(std::chrono::steady_clock::time_point start) {
        return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    }
};

}  // namespace sbx
