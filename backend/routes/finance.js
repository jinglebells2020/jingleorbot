const router = require('express').Router();
const Finance = require('../models/Finance');

router.get('/', async (req, res) => {
  const data = await Finance.find();
  res.json(data);
});

router.post('/', async (req, res) => {
  const item = new Finance(req.body);
  await item.save();
  res.json(item);
});

module.exports = router;
