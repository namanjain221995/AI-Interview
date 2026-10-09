"""Reference programs used by the integration tests (one set per language)."""

from __future__ import annotations

TWO_SUM_SIGNATURE = {
    "function_name": "two_sum",
    "params": [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}],
    "return_type": "int[]",
    "comparison": "exact",
}
TWO_SUM_TESTS = [
    {"id": "t1", "args": [[2, 7, 11, 15], 9], "expected": [0, 1]},
    {"id": "t2", "args": [[3, 2, 4], 6], "expected": [1, 2]},
    {"id": "h1", "args": [[3, 3], 6], "expected": [0, 1], "hidden": True},
    {"id": "h2", "args": [[-1, -2, -3, -4, -5], -8], "expected": [2, 4], "hidden": True},
]

LANGUAGES = ("python", "javascript", "typescript", "java", "go", "cpp")

CORRECT = {
    "python": """
def two_sum(nums, target):
    seen = {}
    for i, n in enumerate(nums):
        if target - n in seen:
            return [seen[target - n], i]
        seen[n] = i
    return []
""",
    "javascript": """
function twoSum(nums, target) {
  const seen = new Map();
  for (let i = 0; i < nums.length; i++) {
    if (seen.has(target - nums[i])) return [seen.get(target - nums[i]), i];
    seen.set(nums[i], i);
  }
  return [];
}
""",
    "typescript": """
function twoSum(nums: number[], target: number): number[] {
  const seen = new Map<number, number>();
  for (let i = 0; i < nums.length; i++) {
    const j = seen.get(target - nums[i]);
    if (j !== undefined) return [j, i];
    seen.set(nums[i], i);
  }
  return [];
}
""",
    "java": """
import java.util.*;

class Solution {
    public int[] twoSum(int[] nums, int target) {
        Map<Integer, Integer> seen = new HashMap<>();
        for (int i = 0; i < nums.length; i++) {
            Integer j = seen.get(target - nums[i]);
            if (j != null) return new int[] {j, i};
            seen.put(nums[i], i);
        }
        return new int[0];
    }
}
""",
    "go": """
package main

func twoSum(nums []int, target int) []int {
	seen := map[int]int{}
	for i, n := range nums {
		if j, ok := seen[target-n]; ok {
			return []int{j, i}
		}
		seen[n] = i
	}
	return nil
}
""",
    "cpp": """
class Solution {
public:
    vector<int> twoSum(vector<int>& nums, int target) {
        unordered_map<int, int> seen;
        for (int i = 0; i < (int)nums.size(); i++) {
            auto it = seen.find(target - nums[i]);
            if (it != seen.end()) return {it->second, i};
            seen[nums[i]] = i;
        }
        return {};
    }
};
""",
}

WRONG = {
    "python": "def two_sum(nums, target):\n    return [0, 0]\n",
    "javascript": "function twoSum(nums, target) { return [0, 0]; }\n",
    "typescript": "function twoSum(nums: number[], target: number): number[] { return [0, 0]; }\n",
    "java": "class Solution { public int[] twoSum(int[] nums, int target) { return new int[] {0, 0}; } }\n",
    "go": "package main\n\nfunc twoSum(nums []int, target int) []int { return []int{0, 0} }\n",
    "cpp": "class Solution { public: vector<int> twoSum(vector<int>& nums, int target) { return {0, 0}; } };\n",
}

COMPILE_ERROR = {
    "python": "def two_sum(nums, target)\n    return []\n",
    "javascript": "function twoSum(nums, target) {\n  return [0, 0;\n}\n",
    "typescript": "function twoSum(nums: number[], target: number): number[] {\n  return [0, 0;\n}\n",
    "java": "class Solution {\n    public int[] twoSum(int[] nums, int target) {\n        return new int[] {0, 0}\n    }\n}\n",
    "go": "package main\n\nfunc twoSum(nums []int, target int) []int {\n\treturn undefinedThing\n}\n",
    "cpp": "class Solution {\npublic:\n    vector<int> twoSum(vector<int>& nums, int target) {\n        return {0, 0}\n    }\n};\n",
}
# Line the compiler should point at.
COMPILE_ERROR_LINE = {
    "python": "line 1",
    "javascript": "solution.js:2",
    "typescript": "solution.ts:2",
    "java": "Solution.java:3",
    "go": "solution.go:4",
    "cpp": "solution.cpp:5",
}

RUNTIME_ERROR = {
    "python": "def two_sum(nums, target):\n    return [nums[100]]\n",
    "javascript": "function twoSum(nums, target) {\n  return [nums[0].missing.field];\n}\n",
    "typescript": "function twoSum(nums: number[], target: number): number[] {\n  throw new Error('boom');\n}\n",
    "java": "class Solution {\n    public int[] twoSum(int[] nums, int target) {\n        return new int[] {nums[100]};\n    }\n}\n",
    "go": "package main\n\nfunc twoSum(nums []int, target int) []int {\n\treturn []int{nums[100]}\n}\n",
    "cpp": "class Solution {\npublic:\n    vector<int> twoSum(vector<int>& nums, int target) {\n        return {nums.at(100)};\n    }\n};\n",
}
RUNTIME_ERROR_TEXT = {
    "python": "IndexError",
    "javascript": "TypeError",
    "typescript": "boom",
    "java": "ArrayIndexOutOfBoundsException",
    "go": "index out of range",
    "cpp": "out_of_range",
}

STDIN_PROGRAM = {
    "python": "a, b = map(int, input().split())\nprint(a + b)\n",
    "javascript": "const [a, b] = require('fs').readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\nconsole.log(a + b);\n",
    "typescript": (
        "import { readFileSync } from 'node:fs';\n"
        "const [a, b]: number[] = readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\n"
        "console.log(a + b);\n"
    ),
    "java": (
        "import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n"
        "        Scanner in = new Scanner(System.in);\n        System.out.println(in.nextLong() + in.nextLong());\n    }\n}\n"
    ),
    "go": 'package main\n\nimport "fmt"\n\nfunc main() {\n\tvar a, b int\n\tfmt.Scan(&a, &b)\n\tfmt.Println(a + b)\n}\n',
    "cpp": "#include <iostream>\nint main() {\n    long long a, b;\n    std::cin >> a >> b;\n    std::cout << a + b << std::endl;\n}\n",
}

# ---------------------------------------------------------------- type coverage
CHECKSUM_SIGNATURE = {
    "function_name": "check_sum",
    "params": [
        {"name": "a", "type": "int"},
        {"name": "b", "type": "long"},
        {"name": "c", "type": "float"},
        {"name": "d", "type": "bool"},
        {"name": "e", "type": "string"},
        {"name": "f", "type": "int[]"},
        {"name": "g", "type": "long[]"},
        {"name": "h", "type": "float[]"},
        {"name": "i", "type": "string[]"},
        {"name": "j", "type": "bool[]"},
        {"name": "k", "type": "int[][]"},
        {"name": "l", "type": "string[][]"},
    ],
    "return_type": "long",
    "comparison": "exact",
}
CHECKSUM_ARGS = [
    3,
    10_000_000_000,
    2.5,
    True,
    "hello",
    [1, 2, 3],
    [4_000_000_000, 5],
    [0.5, 1.5],
    ["ab", "cde"],
    [True, False, True],
    [[1, 2], [3]],
    [["x", "yz"], ["w"]],
]
CHECKSUM_EXPECTED = 3 + 10_000_000_000 + 25 + 1 + 5 + 6 + 4_000_000_005 + 20 + 5 + 2 + 6 + 4

CHECKSUM = {
    "python": """
def check_sum(a, b, c, d, e, f, g, h, i, j, k, l):
    return (a + b + round(c * 10) + (1 if d else 0) + len(e) + sum(f) + sum(g) + round(sum(h) * 10)
            + sum(len(s) for s in i) + sum(1 for x in j if x) + sum(sum(r) for r in k)
            + sum(len(s) for r in l for s in r))
""",
    "javascript": """
function checkSum(a, b, c, d, e, f, g, h, i, j, k, l) {
  const sum = (xs) => xs.reduce((p, q) => p + q, 0);
  return a + b + Math.round(c * 10) + (d ? 1 : 0) + e.length + sum(f) + sum(g) + Math.round(sum(h) * 10)
    + sum(i.map((s) => s.length)) + j.filter(Boolean).length + sum(k.map(sum)) + sum(l.flat().map((s) => s.length));
}
""",
    "typescript": """
function checkSum(a: number, b: number, c: number, d: boolean, e: string, f: number[], g: number[], h: number[],
                  i: string[], j: boolean[], k: number[][], l: string[][]): number {
  const sum = (xs: number[]): number => xs.reduce((p, q) => p + q, 0);
  return a + b + Math.round(c * 10) + (d ? 1 : 0) + e.length + sum(f) + sum(g) + Math.round(sum(h) * 10)
    + sum(i.map((s) => s.length)) + j.filter(Boolean).length + sum(k.map(sum)) + sum(l.flat().map((s) => s.length));
}
""",
    "java": """
class Solution {
    public long checkSum(int a, long b, double c, boolean d, String e, int[] f, long[] g, double[] h,
                         String[] i, boolean[] j, int[][] k, String[][] l) {
        long t = a + b + Math.round(c * 10) + (d ? 1 : 0) + e.length();
        for (int x : f) t += x;
        for (long x : g) t += x;
        double hs = 0;
        for (double x : h) hs += x;
        t += Math.round(hs * 10);
        for (String s : i) t += s.length();
        for (boolean x : j) if (x) t++;
        for (int[] r : k) for (int x : r) t += x;
        for (String[] r : l) for (String s : r) t += s.length();
        return t;
    }
}
""",
    "go": """
package main

import "math"

func checkSum(a int, b int64, c float64, d bool, e string, f []int, g []int64, h []float64,
	i []string, j []bool, k [][]int, l [][]string) int64 {
	t := int64(a) + b + int64(math.Round(c*10)) + int64(len(e))
	if d {
		t++
	}
	for _, x := range f {
		t += int64(x)
	}
	for _, x := range g {
		t += x
	}
	hs := 0.0
	for _, x := range h {
		hs += x
	}
	t += int64(math.Round(hs * 10))
	for _, s := range i {
		t += int64(len(s))
	}
	for _, x := range j {
		if x {
			t++
		}
	}
	for _, r := range k {
		for _, x := range r {
			t += int64(x)
		}
	}
	for _, r := range l {
		for _, s := range r {
			t += int64(len(s))
		}
	}
	return t
}
""",
    "cpp": """
class Solution {
public:
    long long checkSum(int a, long long b, double c, bool d, string e, vector<int>& f, vector<long long>& g,
                       vector<double>& h, vector<string>& i, vector<bool>& j, vector<vector<int>>& k,
                       vector<vector<string>>& l) {
        long long t = a + b + llround(c * 10) + (d ? 1 : 0) + (long long)e.size();
        for (int x : f) t += x;
        for (long long x : g) t += x;
        double hs = 0;
        for (double x : h) hs += x;
        t += llround(hs * 10);
        for (auto& s : i) t += s.size();
        for (bool x : j) if (x) t++;
        for (auto& r : k) for (int x : r) t += x;
        for (auto& r : l) for (auto& s : r) t += s.size();
        return t;
    }
};
""",
}

WRAP_SIGNATURE = {
    "function_name": "wrap_words",
    "params": [{"name": "words", "type": "string[]"}, {"name": "flag", "type": "bool"}],
    "return_type": "string[][]",
    "comparison": "exact",
}
WRAP_WORDS = ['say "hi"', "back\\slash", "new\nline\ttab", "emoji 😀", "café", ""]
WRAP_EXPECTED = [[w, w] for w in WRAP_WORDS]

WRAP = {
    "python": "def wrap_words(words, flag):\n    return [[w, w] for w in words] if flag else []\n",
    "javascript": "function wrapWords(words, flag) { return flag ? words.map((w) => [w, w]) : []; }\n",
    "typescript": "function wrapWords(words: string[], flag: boolean): string[][] { return flag ? words.map((w) => [w, w]) : []; }\n",
    "java": (
        "import java.util.*;\n\nclass Solution {\n    public List<List<String>> wrapWords(String[] words, boolean flag) {\n"
        "        List<List<String>> out = new ArrayList<>();\n        if (flag) for (String w : words) out.add(List.of(w, w));\n"
        "        return out;\n    }\n}\n"
    ),
    "go": (
        "package main\n\nfunc wrapWords(words []string, flag bool) [][]string {\n\tvar out [][]string\n"
        "\tif flag {\n\t\tfor _, w := range words {\n\t\t\tout = append(out, []string{w, w})\n\t\t}\n\t}\n\treturn out\n}\n"
    ),
    "cpp": (
        "class Solution {\npublic:\n    vector<vector<string>> wrapWords(vector<string>& words, bool flag) {\n"
        "        vector<vector<string>> out;\n        if (flag) for (auto& w : words) out.push_back({w, w});\n        return out;\n    }\n};\n"
    ),
}

AVERAGE_SIGNATURE = {
    "function_name": "average",
    "params": [{"name": "nums", "type": "float[]"}],
    "return_type": "float",
    "comparison": "float_tolerance",
}
AVERAGE = {
    "python": "def average(nums):\n    return sum(nums) / len(nums)\n",
    "javascript": "function average(nums) { return nums.reduce((a, b) => a + b, 0) / nums.length; }\n",
    "typescript": "function average(nums: number[]): number { return nums.reduce((a, b) => a + b, 0) / nums.length; }\n",
    "java": "class Solution { public double average(double[] nums) { double s = 0; for (double x : nums) s += x; return s / nums.length; } }\n",
    "go": "package main\n\nfunc average(nums []float64) float64 {\n\ts := 0.0\n\tfor _, x := range nums {\n\t\ts += x\n\t}\n\treturn s / float64(len(nums))\n}\n",
    "cpp": "class Solution { public: double average(vector<double>& nums) { double s = 0; for (double x : nums) s += x; return s / nums.size(); } };\n",
}
