// Cache-warming program built once at image build time: it imports the
// standard-library packages interview solutions (and the harness) commonly
// use so their compiled archives are in the read-only GOCACHE baked into the
// image. Candidate builds then only compile their own package and link
// (~0.2 s instead of several seconds).
package main

import (
	"bufio"
	"bytes"
	"cmp"
	"container/heap"
	"container/list"
	"container/ring"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"math"
	"math/big"
	"math/bits"
	"math/rand"
	"os"
	"reflect"
	"regexp"
	"runtime/debug"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode"
	"unicode/utf8"
)

var _ = []any{
	bufio.NewReader, bytes.NewBuffer, cmp.Compare[int], heap.Init, list.New, ring.New, json.Marshal,
	errors.New, fmt.Println, io.ReadAll, maps.Keys[map[int]int], math.Abs, big.NewInt, bits.Len,
	rand.Intn, os.Exit, reflect.TypeOf, regexp.MustCompile, debug.SetMaxStack, slices.Sort[[]int],
	sort.Ints, strconv.Itoa, strings.Split, time.Now, unicode.IsDigit, utf8.RuneLen,
}

var (
	_ sync.Mutex
	_ atomic.Int64
)

func main() {}
