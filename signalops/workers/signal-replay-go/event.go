package main

import (
	"errors"
	"time"
)

type SignalEvent struct {
	ID               string    `json:"id"`
	SchemaVersion    int       `json:"schema_version"`
	OccurredAt       time.Time `json:"occurred_at"`
	Instrument       string    `json:"instrument"`
	StrategyCode     string    `json:"strategy_code"`
	StrategyName     string    `json:"strategy_name"`
	PreviousPosition int       `json:"previous_position"`
	NewPosition      int       `json:"new_position"`
	Action           string    `json:"action"`
	Quantity         float64   `json:"quantity"`
}

func (event SignalEvent) Validate() error {
	if event.ID == "" || event.StrategyCode == "" || event.Instrument == "" {
		return errors.New("事件缺少必要識別欄位")
	}
	if event.SchemaVersion != 1 {
		return errors.New("只支援 SignalEvent v1")
	}
	if event.Quantity <= 0 {
		return errors.New("事件數量必須大於零")
	}
	if event.PreviousPosition < -1 || event.PreviousPosition > 1 ||
		event.NewPosition < -1 || event.NewPosition > 1 {
		return errors.New("持倉必須介於 -1 與 1")
	}
	valid := false
	switch event.Action {
	case "enter":
		valid = event.PreviousPosition == 0 && event.NewPosition != 0
	case "exit":
		valid = event.PreviousPosition != 0 && event.NewPosition == 0
	case "reverse":
		valid = event.PreviousPosition != 0 && event.NewPosition == -event.PreviousPosition
	}
	if !valid {
		return errors.New("事件動作與持倉轉換不一致")
	}
	return nil
}
