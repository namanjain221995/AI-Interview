package main

import (
	"fmt"
	"sort"
	"strings"
)

func twoSum(nums []int, target int) []int {
	seen := map[int]int{}
	for i, n := range nums {
		if j, ok := seen[target-n]; ok {
			return []int{j, i}
		}
		seen[n] = i
	}
	sort.Ints(nums)
	_ = strings.TrimSpace(fmt.Sprint(nums))
	return nil
}
