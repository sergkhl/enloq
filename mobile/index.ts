// The push task is defined before the router loads, so a push that wakes a
// closed app finds it (expo-notifications runs the bundle without a screen).
import "./src/pushTask";
import "expo-router/entry";
