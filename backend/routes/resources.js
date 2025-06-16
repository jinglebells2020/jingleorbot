const router = require('express').Router();
const Resource = require('../models/Resource');

router.get('/', async (req, res) => {
  const resources = await Resource.find();
  res.json(resources);
});

router.post('/', async (req, res) => {
  const resource = new Resource(req.body);
  await resource.save();
  res.json(resource);
});

module.exports = router;
