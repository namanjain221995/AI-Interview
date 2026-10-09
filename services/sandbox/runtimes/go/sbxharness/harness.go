// Package sbxharness is the function-call harness for Go submissions.
//
// It lives in its own package of the sandbox module so its compiled archive
// is part of the GOCACHE baked into the image: a test-mode build only
// compiles the candidate's package main, whose generated entry.go is
// `func main() { sbxharness.Run(twoSum) }`. Run reads the test payload from
// stdin, converts each JSON argument to the entry point's declared parameter
// type with encoding/json, calls it once per test (recovering panics) and
// writes JSON records to fd 3. Expected values never reach this process: the
// sandbox service compares the returned values.
package sbxharness

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"reflect"
	"runtime/debug"
	"sort"
	"strconv"
	"strings"
	"time"
)

type sbxTest struct {
	I    int               `json:"i"`
	Args []json.RawMessage `json:"args"`
}

type sbxInput struct {
	EntryCandidates []string  `json:"entry_candidates"`
	Tests           []sbxTest `json:"tests"`
}

var sbxResults = os.NewFile(3, "results")

func sbxEmit(record map[string]any) {
	data, err := json.Marshal(record)
	if err != nil {
		data, _ = json.Marshal(map[string]any{"t": "result", "i": record["i"], "ok": false, "error_type": "runtime", "error": "unreportable value: " + err.Error()})
	}
	data = append(data, '\n')
	for len(data) > 0 {
		n, werr := sbxResults.Write(data)
		if werr != nil {
			os.Exit(3)
		}
		data = data[n:]
	}
}

type sbxRaw string

func (r sbxRaw) MarshalJSON() ([]byte, error) { return []byte(r), nil }

func sbxEncode(b *strings.Builder, v reflect.Value, depth int) error {
	if depth > 200 {
		return fmt.Errorf("return value is nested too deeply")
	}
	if !v.IsValid() {
		b.WriteString("null")
		return nil
	}
	switch v.Kind() {
	case reflect.Bool:
		b.WriteString(strconv.FormatBool(v.Bool()))
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		b.WriteString(strconv.FormatInt(v.Int(), 10))
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr:
		b.WriteString(strconv.FormatUint(v.Uint(), 10))
	case reflect.Float32, reflect.Float64:
		f := v.Float()
		switch {
		case math.IsNaN(f):
			b.WriteString(`"NaN"`)
		case math.IsInf(f, 1):
			b.WriteString(`"Infinity"`)
		case math.IsInf(f, -1):
			b.WriteString(`"-Infinity"`)
		default:
			bits := 64
			if v.Kind() == reflect.Float32 {
				bits = 32
			}
			b.WriteString(strconv.FormatFloat(f, 'g', -1, bits))
		}
	case reflect.String:
		data, _ := json.Marshal(v.String())
		b.Write(data)
	case reflect.Slice, reflect.Array:
		b.WriteByte('[')
		for i := 0; i < v.Len(); i++ {
			if i > 0 {
				b.WriteByte(',')
			}
			if err := sbxEncode(b, v.Index(i), depth+1); err != nil {
				return err
			}
		}
		b.WriteByte(']')
	case reflect.Map:
		keys := v.MapKeys()
		names := make([]string, len(keys))
		for i, k := range keys {
			names[i] = fmt.Sprint(k.Interface())
		}
		idx := make([]int, len(keys))
		for i := range idx {
			idx[i] = i
		}
		sort.Slice(idx, func(a, c int) bool { return names[idx[a]] < names[idx[c]] })
		b.WriteByte('{')
		for n, i := range idx {
			if n > 0 {
				b.WriteByte(',')
			}
			data, _ := json.Marshal(names[i])
			b.Write(data)
			b.WriteByte(':')
			if err := sbxEncode(b, v.MapIndex(keys[i]), depth+1); err != nil {
				return err
			}
		}
		b.WriteByte('}')
	case reflect.Pointer, reflect.Interface:
		if v.IsNil() {
			b.WriteString("null")
			return nil
		}
		return sbxEncode(b, v.Elem(), depth+1)
	default:
		data, err := json.Marshal(v.Interface())
		if err != nil {
			return fmt.Errorf("cannot report a value of type %s", v.Type())
		}
		b.Write(data)
	}
	return nil
}

func sbxDescribePanic(r any, stack []byte) string {
	var msg string
	switch e := r.(type) {
	case error:
		msg = e.Error()
	default:
		msg = fmt.Sprint(e)
	}
	lines := strings.Split(string(stack), "\n")
	var frames []string
	for i := 0; i+1 < len(lines); i++ {
		loc := strings.TrimSpace(lines[i+1])
		if strings.Contains(loc, "solution.go:") {
			fn := strings.TrimSpace(lines[i])
			if p := strings.LastIndex(fn, "("); p > 0 {
				fn = fn[:p]
			}
			fn = strings.TrimPrefix(fn, "main.")
			if sp := strings.Index(loc, " +0x"); sp > 0 {
				loc = loc[:sp]
			}
			if slash := strings.LastIndex(loc, "/"); slash >= 0 {
				loc = loc[slash+1:]
			}
			frames = append(frames, "    at "+fn+" ("+loc+")")
			if len(frames) >= 16 {
				break
			}
		}
	}
	out := "panic: " + msg
	if len(frames) > 0 {
		out += "\n" + strings.Join(frames, "\n")
	}
	return out
}

func sbxCall(fn reflect.Value, args []reflect.Value) (out []reflect.Value, perr any, stack []byte) {
	defer func() {
		if r := recover(); r != nil {
			perr = r
			stack = debug.Stack()
		}
	}()
	out = fn.Call(args)
	return
}

// Run executes every test in the stdin payload against entry and exits.
func Run(entry any) {
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		sbxEmit(map[string]any{"t": "fatal", "kind": "harness", "message": "could not read test input: " + err.Error()})
		os.Exit(1)
	}
	if devnull, derr := os.Open(os.DevNull); derr == nil {
		os.Stdin = devnull
	}
	var input sbxInput
	if err := json.Unmarshal(raw, &input); err != nil {
		sbxEmit(map[string]any{"t": "fatal", "kind": "harness", "message": "invalid test input: " + err.Error()})
		os.Exit(1)
	}
	fn := reflect.ValueOf(entry)
	if fn.Kind() != reflect.Func {
		sbxEmit(map[string]any{"t": "fatal", "kind": "entry", "message": "entry point is not a function"})
		os.Exit(1)
	}
	ft := fn.Type()
	sbxEmit(map[string]any{"t": "ready"})
	for _, test := range input.Tests {
		sbxEmit(map[string]any{"t": "begin", "i": test.I})
		if len(test.Args) != ft.NumIn() {
			sbxEmit(map[string]any{"t": "result", "i": test.I, "ok": false, "error_type": "harness",
				"error": fmt.Sprintf("bad arguments: function takes %d arguments, test has %d", ft.NumIn(), len(test.Args))})
			continue
		}
		args := make([]reflect.Value, ft.NumIn())
		var argErr error
		for k := 0; k < ft.NumIn(); k++ {
			ptr := reflect.New(ft.In(k))
			if err := json.Unmarshal(test.Args[k], ptr.Interface()); err != nil {
				argErr = fmt.Errorf("argument %d: %v", k+1, err)
				break
			}
			args[k] = ptr.Elem()
		}
		if argErr != nil {
			sbxEmit(map[string]any{"t": "result", "i": test.I, "ok": false, "error_type": "harness", "error": "bad arguments: " + argErr.Error()})
			continue
		}
		started := time.Now()
		out, perr, stack := sbxCall(fn, args)
		ms := float64(time.Since(started).Nanoseconds()) / 1e6
		if perr != nil {
			sbxEmit(map[string]any{"t": "result", "i": test.I, "ok": false, "error_type": "runtime", "error": sbxDescribePanic(perr, stack), "ms": ms})
			continue
		}
		var b strings.Builder
		var value reflect.Value
		if len(out) > 0 {
			value = out[0]
		}
		if err := sbxEncode(&b, value, 0); err != nil {
			sbxEmit(map[string]any{"t": "result", "i": test.I, "ok": false, "error_type": "runtime", "error": "invalid return value: " + err.Error(), "ms": ms})
			continue
		}
		sbxEmit(map[string]any{"t": "result", "i": test.I, "ok": true, "actual": sbxRaw(b.String()), "ms": ms})
	}
	os.Stdout.Sync()
	sbxEmit(map[string]any{"t": "done"})
	os.Exit(0)
}
