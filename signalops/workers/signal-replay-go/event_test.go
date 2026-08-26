package main

import (
	"testing"
	"time"
)

func validEvent() SignalEvent {
	return SignalEvent{
		ID:               "00000000-0000-0000-0000-000000000001",
		SchemaVersion:    1,
		OccurredAt:       time.Date(2026, 8, 26, 0, 0, 0, 0, time.UTC),
		Instrument:       "MXF",
		StrategyCode:     "TEST",
		StrategyName:     "測試策略",
		PreviousPosition: 0,
		NewPosition:      1,
		Action:           "enter",
		Quantity:         1,
	}
}

func TestValidateAcceptsEntry(t *testing.T) {
	event := validEvent()
	if err := event.Validate(); err != nil {
		t.Fatalf("預期合法事件，卻得到錯誤：%v", err)
	}
}

func TestValidateRejectsInvalidTransition(t *testing.T) {
	event := validEvent()
	event.PreviousPosition = -1
	if err := event.Validate(); err == nil {
		t.Fatal("預期拒絕不合法的 enter 轉換")
	}
}

func TestValidateRejectsUnknownSchema(t *testing.T) {
	event := validEvent()
	event.SchemaVersion = 2
	if err := event.Validate(); err == nil {
		t.Fatal("預期拒絕未知 schema 版本")
	}
}

func TestValidateRejectsNonPositiveQuantity(t *testing.T) {
	event := validEvent()
	event.Quantity = 0
	if err := event.Validate(); err == nil {
		t.Fatal("預期拒絕非正數 quantity")
	}
}
