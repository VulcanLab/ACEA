// ACEA MongoDB initialization
// Creates indexes on the events collection for query performance

db = db.getSiblingDB("arena");

// Event log collection indexes
db.events.createIndex({ session_id: 1, round: 1 });
db.events.createIndex({ session_id: 1, event_type: 1 });
db.events.createIndex({ timestamp: 1 }, { expireAfterSeconds: 2592000 }); // 30-day TTL

// Ensure the collection exists
db.events.insertOne({
    _id: "init",
    event_type: "system.init",
    session_id: "system",
    round: 0,
    timestamp: new Date(),
    data: { message: "MongoDB initialized for ACEA" }
});
db.events.deleteOne({ _id: "init" });

print("MongoDB arena database initialized.");
